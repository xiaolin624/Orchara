"""T037: submitTool validation, content identity and the tool_build job.

Nothing here needs the build sandbox: submission inserts the row and exactly one
``tool_build`` background job in one transaction, and that is observed against
the real API and PostgreSQL. The build itself is covered by the sandbox tests.
"""

from __future__ import annotations

import hashlib
import json

from support import f00b, t028, t033
from support import stack as st
from support import t037
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

TOOL_FIELDS = {"id", "state", "approved", "image_digest", "manifest_sha256", "input_schema", "output_schema"}


def creator(stack, admin, login: str):
    return f00b.invite_and_activate(
        stack, admin, login, roles=("user", "creator"), password=f00b.USER_PASSWORD
    )


def assert_validation(response, field: str) -> dict:
    assert response.status == 422, response.text
    body = response.json["error"]
    assert body["code"] == "validation_failed", body
    assert body["details"]["field"] == field, body
    return body


def test_submit_creates_one_submitted_tool_and_exactly_one_build_job(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.owner")
    source = t037.source_zip()
    artifact_id = t037.upload_source_on(t033_stack, owner, source)

    response = t037.submit_tool(owner, t037.submit_body(artifact_id))
    assert response.status == 202, response.text
    accepted = response.json
    assert set(accepted) == {"id", "state"}, accepted
    assert accepted["state"] == "submitted", accepted

    row = t037.tool_row(t033_stack.dbname, accepted["id"])
    assert row["state"] == "submitted"
    assert int(row["revision"]) == 1
    assert row["owner_id"] == owner.principal_id
    assert row["source_artifact_id"] == artifact_id
    assert row["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert len(row["manifest_sha256"]) == 64
    assert row["image_digest"] is None
    assert row["approved_at"] is None
    assert row["build_error_code"] is None
    assert json.loads(row["dependency_lock"]) == t037.DEFAULT_LOCK
    assert json.loads(row["input_schema"]) == t037.DEFAULT_INPUT_SCHEMA
    assert json.loads(row["output_schema"]) == t037.DEFAULT_OUTPUT_SCHEMA
    assert row["connector_versions"] == "[]"
    # The dependency-lock hash is over canonical bytes; {} form is stable.
    expected_lock_sha = hashlib.sha256(t037.canonical_json_bytes(t037.DEFAULT_LOCK)).hexdigest()
    assert row["dependency_lock_sha256"] == expected_lock_sha

    jobs = t037.tool_jobs(t033_stack.dbname, accepted["id"])
    assert len(jobs) == 1, jobs
    job = jobs[0]
    assert job["kind"] == "tool_build"
    assert job["state"] == "ready"
    assert job["dedup_key"] == "tool-build:" + accepted["id"]
    assert job["target_id"] == accepted["id"], job
    assert int(job["expected_revision"]) == 1
    assert job["expected_sha256"] == row["source_sha256"]
    assert job["input_artifact_id"] == artifact_id

    # The build has not run: submitted is honest, not ready.
    fetched = t037.get_tool(owner, accepted["id"])
    assert fetched.status == 200, fetched.text
    assert set(fetched.json) == TOOL_FIELDS, fetched.json
    assert fetched.json["state"] == "submitted"
    assert fetched.json["approved"] is False
    assert fetched.json["image_digest"] is None
    assert fetched.json["manifest_sha256"] == row["manifest_sha256"]


def test_the_same_content_is_idempotent_for_the_owner(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.idem")
    source = t037.source_zip()
    artifact_id = t037.upload_source_on(t033_stack, owner, source)
    body = t037.submit_body(artifact_id)

    first = t037.submit_tool(owner, body)
    assert first.status == 202, first.text
    second = t037.submit_tool(owner, body)
    assert second.status == 202, second.text
    assert second.json["id"] == first.json["id"], (first.json, second.json)
    assert second.json["state"] == "submitted"

    assert len(t037.tool_jobs(t033_stack.dbname, first.json["id"])) == 1
    count = int(
        st.sql(
            t033_stack.dbname,
            "SELECT count(*) FROM platform.tool_versions WHERE manifest_sha256 = "
            f"{st.quote_literal(t037.tool_row(t033_stack.dbname, first.json['id'])['manifest_sha256'])}",
        )
    )
    assert count == 1, count


def test_identical_content_by_another_owner_is_tool_conflict(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    first_owner = creator(t033_stack, admin, "t037.first")
    second_owner = creator(t033_stack, admin, "t037.second")
    source = t037.source_zip()
    first_artifact = t037.upload_source_on(t033_stack, first_owner, source)
    second_artifact = t037.upload_source_on(t033_stack, second_owner, source)

    first = t037.submit_tool(first_owner, t037.submit_body(first_artifact))
    assert first.status == 202, first.text
    conflict = t037.submit_tool(second_owner, t037.submit_body(second_artifact))
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "tool_conflict", conflict.json
    assert len(t037.tool_jobs(t033_stack.dbname, first.json["id"])) == 1


def test_submit_refuses_a_foreign_artifact_as_not_found(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.owner.artifact")
    other = creator(t033_stack, admin, "t037.other.artifact")
    artifact_id = t037.upload_source_on(t033_stack, owner, t037.source_zip())

    response = t037.submit_tool(other, t037.submit_body(artifact_id))
    assert response.status == 404, response.text
    assert response.json["error"]["code"] == "resource_not_found"


def test_submit_refuses_a_source_that_is_not_ready(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.scanning")
    content = t037.source_zip()
    ticket = t037.t028.create_upload(
        owner, media_type="application/zip", size_bytes=len(content), purpose="tool_source"
    ).json
    assert t037.put_link(ticket["upload_url"], content).status in (200, 204)
    # Completion moves it to scanning; no finalize worker runs, so it is not ready.
    completed = t037.t028.complete_upload(owner, ticket["artifact_id"], hashlib.sha256(content).hexdigest())
    assert completed.status == 202, completed.text
    response = t037.submit_tool(owner, t037.submit_body(ticket["artifact_id"]))
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "artifact_not_ready"


def test_submit_refuses_a_non_tool_source_artifact(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.purpose")
    # A real CSV task_input artifact is ready but not a tool build source.
    artifact_id = t033.upload_artifact(
        t033_stack, owner, b"region,total\nEU,10\n", purpose="task_input", media_type="text/csv"
    )
    response = t037.submit_tool(owner, t037.submit_body(artifact_id))
    assert_validation(response, "source_artifact_id")


def test_submit_refuses_non_object_schemas_and_locks_with_located_errors(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.shapes")
    artifact_id = t037.upload_source_on(t033_stack, owner, t037.source_zip())
    base = t037.submit_body(artifact_id)

    for field in ("input_schema", "output_schema", "dependency_lock"):
        body = dict(base)
        body[field] = ["not", "an", "object"]
        assert_validation(t037.submit_tool(owner, body), field)
        body = dict(base)
        body[field] = None
        assert_validation(t037.submit_tool(owner, body), field)

    body = dict(base)
    body["risk_class"] = "dangerous"
    assert_validation(t037.submit_tool(owner, body), "risk_class")

    body = dict(base)
    body["risk_class"] = "pure"
    body["connector_version_ids"] = [t037.new_uuid()]
    body = dict(body)
    assert_validation(t037.submit_tool(owner, body), "connector_version_ids")

    # The contract body has no extra fields.
    unknown = dict(base)
    unknown["shell"] = "rm -rf /"
    response = owner.client.post("/api/v1/tools", body=unknown, csrf=owner.csrf)
    assert response.status == 400, response.text
    assert response.json["error"]["code"] == "invalid_json"


def test_submit_requires_creator_session_or_write_scope_key(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    plain_user = f00b.invite_and_activate(
        t033_stack, admin, "t037.user", roles=("user",), password=f00b.USER_PASSWORD
    )
    owner = creator(t033_stack, admin, "t037.apikey")
    artifact_id = t037.upload_source_on(t033_stack, owner, t037.source_zip())

    refused = t037.submit_tool(plain_user, t037.submit_body(artifact_id))
    assert refused.status == 403, refused.text
    assert refused.json["error"]["code"] == "creator_role_required"

    # An API key without the scope is refused before any row lookup.
    service_account = t028.create_service_account(admin, "t037 service", owner.principal_id)
    _read_id, read_key = t028.create_api_key(admin, service_account, ["application:read"])
    no_scope = owner.client.post(
        "/api/v1/tools",
        body=t037.submit_body(artifact_id),
        headers={"Authorization": "Bearer " + read_key},
    )
    assert no_scope.status == 403, no_scope.text
    assert no_scope.json["error"]["code"] == "insufficient_scope"

    # A write-scoped key submits as its service account, so the source upload
    # belongs to the service account too.
    _write_id, write_key = t028.create_api_key(admin, service_account, ["application:write"])
    content = t037.source_zip({"tool/other.py": b"OTHER = 2\n"})
    created = owner.client.post(
        "/api/v1/artifacts/uploads",
        body={"media_type": "application/zip", "size_bytes": len(content), "purpose": "tool_source"},
        headers={"Authorization": "Bearer " + write_key},
    )
    assert created.status == 201, created.text
    ticket = created.json
    assert t037.put_link(ticket["upload_url"], content).status in (200, 204)
    completed = owner.client.post(
        f"/api/v1/artifacts/{ticket['artifact_id']}/complete",
        body={"sha256": hashlib.sha256(content).hexdigest()},
        headers={"Authorization": "Bearer " + write_key},
    )
    assert completed.status == 202, completed.text
    t028.finalize_once(t033_stack)
    t037.wait_until(
        lambda: t028.artifact_row(t033_stack.dbname, ticket["artifact_id"])["state"] == "ready",
        timeout=30,
    )
    submitted = owner.client.post(
        "/api/v1/tools",
        body=t037.submit_body(ticket["artifact_id"]),
        headers={"Authorization": "Bearer " + write_key},
    )
    assert submitted.status == 202, submitted.text
    assert t037.tool_row(t033_stack.dbname, submitted.json["id"])["owner_id"] == service_account


def test_list_and_get_follow_owner_admin_approved_visibility(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.visible")
    other = creator(t033_stack, admin, "t037.outsider")
    artifact_id = t037.upload_source_on(t033_stack, owner, t037.source_zip())
    tool_id = t037.submit_tool(owner, t037.submit_body(artifact_id)).json["id"]

    owner_list = t037.list_tools(owner)
    assert owner_list.status == 200, owner_list.text
    assert set(owner_list.json) == {"items", "next_cursor"}
    assert [item["id"] for item in owner_list.json["items"]] == [tool_id]
    for item in owner_list.json["items"]:
        assert set(item) == TOOL_FIELDS, item

    admin_list = t037.list_tools(admin)
    assert [item["id"] for item in admin_list.json["items"]] == [tool_id]

    outsider_list = t037.list_tools(other)
    assert outsider_list.status == 200 and outsider_list.json["items"] == [], outsider_list.json
    hidden = t037.get_tool(other, tool_id)
    assert hidden.status == 404, hidden.text
    assert hidden.json["error"]["code"] == "resource_not_found"
    # The non-contract build facts follow the same rule.
    hidden_facts = t037.get_build(other, tool_id)
    assert hidden_facts.status == 404, hidden_facts.text

    # Approval (by SQL here; the review tests use the endpoint) makes it visible.
    st.sql(
        t033_stack.dbname,
        "UPDATE platform.tool_versions SET approved_by = "
        f"{st.quote_literal(admin.principal_id)}::uuid, approved_at = now() "
        f"WHERE id = {st.quote_literal(tool_id)}::uuid",
    )
    approved = t037.list_tools(other)
    assert [item["id"] for item in approved.json["items"]] == [tool_id]
    assert approved.json["items"][0]["approved"] is True
    visible = t037.get_tool(other, tool_id)
    assert visible.status == 200, visible.text
    # Build artifacts are still owner-only diagnostics for a non-admin outsider.
    still_hidden = t037.get_build(other, tool_id)
    assert still_hidden.status == 404, still_hidden.text
    admin_facts = t037.get_build(admin, tool_id)
    assert admin_facts.status == 200, admin_facts.text


def test_list_cursor_is_stable_and_validated(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.cursor")
    first_artifact = t037.upload_source_on(t033_stack, owner, t037.source_zip())
    first = t037.submit_tool(owner, t037.submit_body(first_artifact)).json["id"]
    second_source = t037.source_zip({"tool/other.py": b"OTHER = 1\n"})
    second_artifact = t037.upload_source_on(t033_stack, owner, second_source)
    second = t037.submit_tool(owner, t037.submit_body(second_artifact, risk_class="read")).json["id"]

    page = t037.list_tools(owner, limit=1)
    assert page.status == 200, page.text
    assert len(page.json["items"]) == 1
    assert page.json["next_cursor"], page.json
    next_page = t037.list_tools(owner, cursor=page.json["next_cursor"], limit=1)
    assert next_page.status == 200, next_page.text
    ids = {page.json["items"][0]["id"], next_page.json["items"][0]["id"]}
    assert ids == {first, second}, ids

    invalid = t037.list_tools(owner, cursor="***")
    assert invalid.status == 400, invalid.text
    assert invalid.json["error"]["code"] == "invalid_cursor"
    bad_limit = t037.list_tools(owner, limit=1000)
    assert bad_limit.status == 422, bad_limit.text
    assert bad_limit.json["error"]["details"]["field"] == "limit"


def test_a_source_zip_is_still_refused_for_other_upload_purposes(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.zip.purpose")
    content = t037.source_zip()
    refused = t037.t028.create_upload(
        owner, media_type="application/zip", size_bytes=len(content), purpose="task_input"
    )
    assert refused.status == 422, refused.text
    assert refused.json["error"]["details"]["field"] == "media_type"
