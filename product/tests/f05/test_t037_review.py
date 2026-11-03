"""T037: admin review binds the manifest hash; reject keeps the build; revoke blocks.

The review endpoint is real HTTP with the real admin session/CSRF rules. Every
assertion reads PostgreSQL (approval/review/revocation facts, audit rows) and the
T025 dependency closure through the real compile/validate/debug endpoints:

  * an unapproved ready tool is usable only in the author's private debug;
  * shared execution requires approved_at;
  * a revoked tool reports dependency_revoked everywhere;
  * a review can never swap the manifest hash it was asked to approve.
"""

from __future__ import annotations

import json

import pytest

from support import f00b, t028, t037
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)


@pytest.fixture(scope="module", autouse=True)
def require_sandbox() -> None:
    if not t037.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version' failed"
        )


def creator(stack, admin, login: str):
    return f00b.invite_and_activate(
        stack, admin, login, roles=("user", "creator"), password=f00b.USER_PASSWORD
    )


def ready_tool(stack, session, *, source: bytes | None = None) -> dict:
    artifact_id = t037.upload_source_on(stack, session, source or t037.source_zip())
    response = t037.submit_tool(session, t037.submit_body(artifact_id))
    assert response.status == 202, response.text
    tool = response.json
    completed, _reports = t037.run_build_once(stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    t037.wait_tool_state(stack.dbname, tool["id"], {"ready"}, timeout=180)
    return tool


def audit_rows(dbname: str, tool_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT action, actor_id::text AS actor_id, resource_type, metadata::text AS metadata "
        "FROM platform.audit_events "
        f"WHERE resource_type = 'tool_version' AND resource_id = {st.quote_literal(tool_id)}::uuid "
        "ORDER BY created_at, id",
    )


def test_review_approves_the_exact_manifest_hash_and_audits_once(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.approve")
    tool = ready_tool(t033_stack, owner)
    row = t037.tool_row(t033_stack.dbname, tool["id"])
    manifest = row["manifest_sha256"]

    approved = t037.review(admin, tool["id"], decision="approve", manifest_sha256=manifest,
                           reason="reviewed the build")
    assert approved.status == 200, approved.text
    assert approved.json == {"id": tool["id"], "state": "ready"}, approved.json

    row = t037.tool_row(t033_stack.dbname, tool["id"])
    assert row["approved_at"] is not None
    assert row["approved_by"] == admin.principal_id
    assert row["review_decision"] == "approved"
    assert row["review_reason"] == "reviewed the build"
    assert row["reviewed_at"] is not None
    assert row["state"] == "ready"
    assert int(row["revision"]) >= 3

    audits = audit_rows(t033_stack.dbname, tool["id"])
    assert [item["action"] for item in audits] == ["tool.approved"], audits
    metadata = json.loads(audits[0]["metadata"])
    assert metadata["manifest_sha256"] == manifest
    assert metadata["reason"] == "reviewed the build"

    # Same decision+reason replays idempotently: no second audit row, no state churn.
    replayed = t037.review(admin, tool["id"], decision="approve", manifest_sha256=manifest,
                           reason="reviewed the build")
    assert replayed.status == 200, replayed.text
    assert len(audit_rows(t033_stack.dbname, tool["id"])) == 1
    # A changed reason is a different review intention, and a different decision
    # can never overwrite an approval.
    changed = t037.review(admin, tool["id"], decision="approve", manifest_sha256=manifest,
                          reason="another reason")
    assert changed.status == 409, changed.text
    assert changed.json["error"]["code"] == "review_conflict"
    reject = t037.review(admin, tool["id"], decision="reject", manifest_sha256=manifest,
                         reason="changed my mind")
    assert reject.status == 409, reject.text
    assert reject.json["error"]["code"] == "review_conflict"

    fetched = t037.get_tool(owner, tool["id"])
    assert fetched.json["approved"] is True


def test_review_refuses_a_hash_mismatch_before_writing_anything(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.hash")
    tool = ready_tool(t033_stack, owner)
    row = t037.tool_row(t033_stack.dbname, tool["id"])
    before = int(row["revision"])

    wrong = "f" * 64
    assert wrong != row["manifest_sha256"]
    conflict = t037.review(admin, tool["id"], decision="approve", manifest_sha256=wrong)
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "review_conflict"
    after = t037.tool_row(t033_stack.dbname, tool["id"])
    assert after["approved_at"] is None
    assert int(after["revision"]) == before
    assert audit_rows(t033_stack.dbname, tool["id"]) == []

    malformed = t037.review(admin, tool["id"], decision="approve", manifest_sha256="not-a-hash")
    assert malformed.status == 422, malformed.text
    assert malformed.json["error"]["details"]["field"] == "manifest_sha256"


def test_review_cas_blocks_a_hash_swapped_after_the_review_was_prepared(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.review.cas")
    tool = ready_tool(t033_stack, owner)
    prepared = t037.tool_row(t033_stack.dbname, tool["id"])["manifest_sha256"]

    # A content change would produce a new manifest identity; simulate the row
    # moving between the admin reading the hash and submitting the decision.
    replacement = "d" * 64
    st.sql(
        t033_stack.dbname,
        "UPDATE platform.tool_versions "
        f"SET manifest_sha256 = {st.quote_literal(replacement)}, revision = revision + 1 "
        f"WHERE id = {st.quote_literal(tool['id'])}::uuid",
    )
    stale = t037.review(admin, tool["id"], decision="approve", manifest_sha256=prepared)
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "review_conflict"
    assert t037.tool_row(t033_stack.dbname, tool["id"])["approved_at"] is None

    current = t037.review(admin, tool["id"], decision="approve", manifest_sha256=replacement)
    assert current.status == 200, current.text
    assert t037.tool_row(t033_stack.dbname, tool["id"])["approved_at"] is not None


def test_review_is_admin_only_and_requires_the_idempotency_header(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.review.authz")
    tool = ready_tool(t033_stack, owner)
    manifest = t037.tool_row(t033_stack.dbname, tool["id"])["manifest_sha256"]

    forbidden = t037.review(owner, tool["id"], decision="approve", manifest_sha256=manifest)
    assert forbidden.status == 403, forbidden.text
    assert forbidden.json["error"]["code"] == "forbidden"

    service_account = t028.create_service_account(admin, "t037 reviewer", owner.principal_id)
    _key_id, key = t028.create_api_key(admin, service_account, ["application:write"])
    bearer = owner.client.post(
        f"/api/v1/tools/{tool['id']}/review",
        body={"decision": "approve", "manifest_sha256": manifest, "reason": "key"},
        headers={"Authorization": "Bearer " + key, "Idempotency-Key": t037.new_idempotency_key()},
    )
    assert bearer.status == 403, bearer.text
    assert bearer.json["error"]["code"] == "forbidden"

    missing = owner.client.post(
        f"/api/v1/tools/{tool['id']}/review",
        body={"decision": "approve", "manifest_sha256": manifest, "reason": "no key"},
        csrf=admin.csrf,
    )
    # The caller is the creator here; authentication/role fails before the header.
    assert missing.status == 403, missing.text
    missing_admin = admin.client.post(
        f"/api/v1/tools/{tool['id']}/review",
        body={"decision": "approve", "manifest_sha256": manifest, "reason": "no key"},
        csrf=admin.csrf,
    )
    assert missing_admin.status == 400, missing_admin.text
    assert missing_admin.json["error"]["code"] == "idempotency_key_required"


def test_review_requires_a_ready_build(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.notready")
    artifact_id = t037.upload_source_on(t033_stack, owner, t037.source_zip())
    tool = t037.submit_tool(owner, t037.submit_body(artifact_id)).json
    manifest = t037.tool_row(t033_stack.dbname, tool["id"])["manifest_sha256"]
    refused = t037.review(admin, tool["id"], decision="approve", manifest_sha256=manifest)
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] == "tool_state_conflict"
    assert t037.tool_row(t033_stack.dbname, tool["id"])["approved_at"] is None


def test_a_rejection_records_the_reason_without_marking_the_build_failed(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.reject")
    tool = ready_tool(t033_stack, owner)
    manifest = t037.tool_row(t033_stack.dbname, tool["id"])["manifest_sha256"]

    rejected = t037.review(admin, tool["id"], decision="reject", manifest_sha256=manifest,
                           reason="the source needs a review")
    assert rejected.status == 200, rejected.text
    row = t037.tool_row(t033_stack.dbname, tool["id"])
    assert row["state"] == "ready", row  # build success is not review success
    assert row["image_digest"], row
    assert row["approved_at"] is None
    assert row["review_decision"] == "rejected"
    assert row["review_reason"] == "the source needs a review"
    assert row["build_error_code"] is None

    audits = audit_rows(t033_stack.dbname, tool["id"])
    assert [item["action"] for item in audits] == ["tool.rejected"], audits
    # A rejected tool cannot then be approved by a different decision.
    approve = t037.review(admin, tool["id"], decision="approve", manifest_sha256=manifest,
                          reason="please approve")
    assert approve.status == 409, approve.text
    assert t037.get_tool(owner, tool["id"]).json["approved"] is False


def test_ready_is_not_approved_shared_and_private_debug_use_the_t025_rules(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.private")
    other = creator(t033_stack, admin, "t037.private.other")
    tool = ready_tool(t033_stack, owner)
    application_id = t037.application_with_tool_definition(owner, tool["id"])

    shared = t037.validate_application(owner, application_id)
    assert shared.status == 200, shared.text
    assert shared.json["valid"] is False, shared.json
    codes = {error["code"] for error in shared.json["errors"]}
    assert codes == {"dependency_not_ready"}, shared.json

    private = t037.submit_debug_run(owner, application_id)
    assert private.status == 202, private.text
    assert private.json["state"] == "queued"

    # Another creator cannot use the author's unapproved tool, even privately.
    other_application = t037.application_with_tool_definition(other, tool["id"])
    other_shared = t037.validate_application(other, other_application)
    assert {error["code"] for error in other_shared.json["errors"]} == {"dependency_not_ready"}
    other_private = t037.submit_debug_run(other, other_application)
    assert other_private.status == 422, other_private.text
    other_errors = other_private.json["error"]["details"]["errors"]
    assert {error["code"] for error in other_errors} == {"dependency_not_visible"}, other_errors

    manifest = t037.tool_row(t033_stack.dbname, tool["id"])["manifest_sha256"]
    assert t037.review(admin, tool["id"], decision="approve", manifest_sha256=manifest).status == 200

    approved_shared = t037.validate_application(owner, application_id)
    assert approved_shared.status == 200, approved_shared.text
    assert approved_shared.json == {"valid": True, "errors": []}, approved_shared.json
    # The other creator may now use the shared approved tool in shared execution.
    other_approved = t037.validate_application(other, other_application)
    assert other_approved.json["valid"] is True, other_approved.json


def test_revoke_blocks_new_dependency_validation_and_keeps_history(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.revoke")
    tool = ready_tool(t033_stack, owner)
    manifest = t037.tool_row(t033_stack.dbname, tool["id"])["manifest_sha256"]
    assert t037.review(admin, tool["id"], decision="approve", manifest_sha256=manifest).status == 200
    application_id = t037.application_with_tool_definition(owner, tool["id"])
    assert t037.validate_application(owner, application_id).json["valid"] is True

    forbidden = t037.revoke(owner, tool["id"], reason="creator cannot")
    assert forbidden.status == 403, forbidden.text

    revoked = t037.revoke(admin, tool["id"], reason="unsafe dependency")
    assert revoked.status == 202, revoked.text
    assert revoked.json == {"id": tool["id"], "state": "revoked"}, revoked.json
    row = t037.tool_row(t033_stack.dbname, tool["id"])
    assert row["state"] == "revoked"
    assert row["revoked_at"] is not None
    assert row["revoked_by"] == admin.principal_id
    assert row["revoke_reason"] == "unsafe dependency"
    assert row["image_digest"] and row["approved_at"] is not None  # history is kept

    audits = [item["action"] for item in audit_rows(t033_stack.dbname, tool["id"])]
    assert audits == ["tool.approved", "tool.revoked"], audits

    # New dependency validation (shared and private) now reports dependency_revoked.
    shared = t037.validate_application(owner, application_id)
    assert shared.status == 200 and shared.json["valid"] is False
    assert {error["code"] for error in shared.json["errors"]} == {"dependency_revoked"}, shared.json
    debug = t037.submit_debug_run(owner, application_id)
    assert debug.status == 422, debug.text
    assert {error["code"] for error in debug.json["error"]["details"]["errors"]} == {
        "dependency_revoked"
    }

    # The row stays readable to its owner and an administrator (history, not data).
    for session in (owner, admin):
        fetched = t037.get_tool(session, tool["id"])
        assert fetched.status == 200, fetched.text
        assert fetched.json["state"] == "revoked"
        assert fetched.json["approved"] is True

    # Revoking again is idempotent (no second audit, same state).
    again = t037.revoke(admin, tool["id"], reason="again")
    assert again.status == 202, again.text
    assert [item["action"] for item in audit_rows(t033_stack.dbname, tool["id"])] == [
        "tool.approved",
        "tool.revoked",
    ]
