"""T068 end-to-end: a real runner report is reviewed and published.

This is the genuine path: the real maintenance runner creates and collects five
sales cases in the real WSL2/runsc sandbox (two normal cases, two schema-rejected
inputs, one over-limit input and one real tool failure produced with a wrong
rootfs digest for that case only), the real completion guard accepts the report,
and the creator then submits it for publication. The approval first refuses a
revoked tool dependency with evidence, and then publishes after the dependency is
restored in this test-only scenario.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from support import t042, t068, t052
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


def test_a_genuine_five_category_report_is_reviewed_and_published(
    t068_stack, tmp_path: Path
) -> None:
    report = t068.genuine_sales_report(
        t068_stack, login="t068-e2e", log_path=tmp_path / "pools.log"
    )
    sealed = {case["id"]: case for case in report["sealed"]["cases"]}
    assert report["sealed"]["status"] == "passed", report["sealed"]["status"]
    assert sealed["sales-happy-csv"]["facts"]["run_state"] == "succeeded"
    assert sealed["sales-bad-schema-1"]["facts"]["run_error_code"] == "node_input_invalid"
    assert sealed["sales-over-limit"]["facts"]["run_error_code"] == "row_limit_exceeded"
    assert sealed["sales-tool-failure"]["facts"]["run_error_code"] == "sandbox_unavailable"
    assert sealed["sales-bad-schema-2"]["facts"]["run_error_code"] == "node_input_invalid"

    session = report["session"]
    application_id = report["application_id"]
    manifest_sha = report["manifest_sha256"]

    preview = t068.preview_publication(session, application_id)
    assert preview.status == 200, preview.text
    assert preview.json["manifest_sha256"] == manifest_sha, preview.json
    assert preview.json["purpose_card"]["purpose"], preview.json
    tools = preview.json["tool_capabilities"]
    assert tools and tools[0]["risk_class"] in {"pure", "read", "write"}, preview.json

    submitted = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report["report"]["id"],
    )
    assert submitted.status == 202, submitted.text
    review_id = submitted.json["id"]
    row = t068.review_row(t068_stack.dbname, review_id)
    snapshot = json.loads(row["snapshot"])
    assert snapshot["author_cases"]["satisfied"] is True, snapshot["author_cases"]
    assert snapshot["author_cases"]["categories"] == CATEGORIES, snapshot["author_cases"]
    assert snapshot["tool_capabilities"], snapshot
    assert snapshot["model_destinations"] == [], snapshot

    admin = t068.admin_session(t068_stack)

    # A real tool dependency, revoked after submission, fails the approval with
    # located evidence; the version transaction does not run.
    t052.st.sql(
        t068_stack.dbname,
        "UPDATE platform.tool_versions SET state = 'revoked', revoked_at = now(), "
        "revoke_reason = 't068 e2e revocation' "
        f"WHERE id = {t052.st.quote_literal(t042.PRESET_TOOL_ID)}::uuid",
    )
    blocked = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256=manifest_sha
    )
    assert blocked.status == 409, blocked.text
    assert blocked.json["error"]["code"] == "dependency_unavailable", blocked.text
    located = blocked.json["error"]["details"]["errors"]
    assert any(error["code"] == "dependency_revoked" for error in located), located
    assert t068.version_count(t068_stack.dbname, application_id) == 0
    assert t068.review_row(t068_stack.dbname, review_id)["state"] == "submitted"

    t052.st.sql(
        t068_stack.dbname,
        "UPDATE platform.tool_versions SET state = 'ready', revoked_at = NULL, "
        "revoke_reason = NULL "
        f"WHERE id = {t052.st.quote_literal(t042.PRESET_TOOL_ID)}::uuid",
    )
    approved = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256=manifest_sha,
        reason="five genuine author cases reviewed",
    )
    assert approved.status == 200, approved.text
    assert approved.json["state"] == "approved", approved.json
    version_id = approved.json["version_id"]
    versions = t068.version_rows(t068_stack.dbname, application_id)
    assert len(versions) == 1 and versions[0]["id"] == version_id, versions
    assert versions[0]["manifest_sha256"] == manifest_sha, versions
    assert t068.active_version_id(t068_stack.dbname, application_id) == version_id
    assert len(t068.outbox_rows(t068_stack.dbname, version_id)) == 1
    assert "publication.approved" in t068.audit_actions(t068_stack.dbname, version_id)

    # The published version is readable by its hash over the contract read model.
    fetched = session.client.get(f"/api/v1/versions/{version_id}")
    assert fetched.status == 200, fetched.text
    assert fetched.json["manifest_sha256"] == manifest_sha, fetched.text
    assert uuid.UUID(fetched.json["id"]), fetched.text
