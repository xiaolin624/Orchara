"""T030 real-stack: the user path for the trusted preset sales tool.

Every request is real HTTP against the compiled API; the input is a real T028
upload sealed by the real artifact_finalize handler; the tool run is executed by
the real ``cmd/maintenance`` process over both real pools (tool_run work pool +
artifact_finalize system_io pool) with the real Python interpreter; and the
summary/log artifacts are fetched back through the owner-only artifact content
endpoint.

The exact Decimal result is asserted for both the published CSV and XLSX
example, and the downloadable summary.csv/summary.json bytes are compared.
"""

from __future__ import annotations

import json
import secrets

import pytest

from support import f00b, t028, t030
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t030_stack,
)


def user(stack: t030.T030Stack, label: str = "owner") -> t030.Session:
    admin = t030.admin_session(stack)
    return t030.invite_and_activate(stack, admin, f"t030-{label}-{secrets.token_hex(3)}")


def require_login(session: t030.Session) -> None:
    assert session.client.get("/api/v1/auth/me").status == 200


def test_csv_and_xlsx_runs_produce_the_exact_decimal_summary(t030_stack) -> None:
    owner = user(t030_stack)
    require_login(owner)

    created_runs: list[tuple[str, str, str]] = []  # (media_type, artifact_id, job_id)
    for media_type, content, key in (
        (t030.CSV, t030.example_csv(), "t030-csv"),
        (t030.XLSX, t030.example_xlsx(), "t030-xlsx"),
    ):
        artifact_id = t030.upload_ready_artifact(t030_stack, owner, content, media_type)
        created = t030.create_tool_run(owner, artifact_id, key=key)
        assert created.status == 202, created.text
        t030.assert_create_view(created.json)
        assert created.json["state"] == "ready"
        assert created.json["artifact_id"] == artifact_id

        job = t030.job_row(t030_stack.dbname, created.json["id"])
        assert job["kind"] == t030.KIND_TOOL_RUN
        assert job["state"] == "ready"
        assert job["owner_id"] == owner.principal_id
        assert job["target_id"] == artifact_id
        assert job["input_artifact_id"] == artifact_id
        assert job["expected_sha256"] == t030.artifact_row(t030_stack.dbname, artifact_id)["sha256"]
        assert int(job["expected_revision"]) == int(
            t030.artifact_row(t030_stack.dbname, artifact_id)["revision"]
        )
        created_runs.append((media_type, artifact_id, created.json["id"]))

    reports = t030.job_reports(t030.run_tool_pools_once(t030_stack)[1])
    assert len(reports) == 2, reports
    assert all(report["result"] == "succeeded" for report in reports), reports

    for _media_type, _artifact_id, job_id in created_runs:
        run = t030.wait_for_run_state(owner, job_id, {"succeeded"})
        t030.assert_run_view(run)
        assert run["state"] == "succeeded"
        assert run["error_code"] is None

        row = t030.job_row(t030_stack.dbname, job_id)
        assert row["result_event_id"]
        assert row["result_artifact_id"], row
        assert row["error_code"] is None
        assert int(row["attempt_count"]) == 1

        artifacts = {artifact["purpose"]: artifact for artifact in run["artifacts"]}
        assert set(artifacts) == {"tool_summary", "tool_artifact", "log"}, artifacts
        summary = artifacts["tool_summary"]
        csv = artifacts["tool_artifact"]
        log = artifacts["log"]
        assert summary["state"] == "ready"
        assert summary["media_type"] == "application/json"
        assert csv["state"] == "ready"
        assert csv["media_type"] == "text/csv"
        assert log["state"] == "ready"
        assert row["result_artifact_id"] == summary["id"]

        summary_document = t030.read_artifact_json(owner, summary["id"])
        assert summary_document == t030.EXPECTED
        # Decimal strings, never JSON numbers.
        assert isinstance(summary_document["total"], str)
        assert all(isinstance(value, str) for value in summary_document["regions"].values())
        assert t030.read_artifact_text(owner, csv["id"]) == (
            "region,total\n东区,200.00\n西区,50.00\n"
        )

        log_text = t030.read_artifact_text(owner, log["id"])
        assert "tool=sales_stats" in log_text
        assert "timed_out=false" in log_text
        # The log artifact is a capped diagnostic, never the input or a secret.
        assert "order_id,date,region,amount" not in log_text
        assert "A1,2026-01-01" not in log_text
        assert "ORCHARA_" not in log_text
        assert str(t030_stack.object_dir) not in log_text
        assert t030_stack.signing_key not in log_text

        # The imported row is the caller's own ready artifact and no diagnostics
        # artifact exists for a successful run.
        summary_row = t028.artifact_row(t030_stack.dbname, summary["id"])
        assert summary_row["owner_id"] == owner.principal_id
        assert summary_row["state"] == "ready"
        assert summary_row["purpose"] == "tool_summary"
        assert t030.diagnostics_row(t030_stack.dbname, job_id) is None

    # Only the owner can read the run; the administrator gets the same 404.
    admin = t030.admin_session(t030_stack)
    job_id = created_runs[0][2]
    for actor in (user(t030_stack, "other"), admin):
        refused = t030.get_tool_run(actor, job_id)
        assert refused.status == 404, refused.text
        assert refused.json["error"]["code"] == "resource_not_found"

    # The content endpoints remain the only byte path, so another user cannot
    # fetch the summary either.
    artifacts = t030.output_artifacts(t030_stack.dbname, job_id)
    summary_id = next(row["id"] for row in artifacts if row["purpose"] == "tool_summary")
    other = user(t030_stack, "reader")
    refused_content = t030.read_artifact_content(other, summary_id)
    assert refused_content.status == 404, refused_content.text


def test_create_is_idempotent_and_the_artifact_admission_is_strict(t030_stack) -> None:
    owner = user(t030_stack)
    other = user(t030_stack, "other")
    artifact_id = t030.upload_ready_artifact(t030_stack, owner, t030.example_csv())
    key = "t030-idempotent"

    first = t030.create_tool_run(owner, artifact_id, key=key)
    assert first.status == 202, first.text
    t030.assert_create_view(first.json)
    job_id = first.json["id"]

    replay = t030.create_tool_run(owner, artifact_id, key=key)
    assert replay.status == 202, replay.text
    assert replay.json == first.json
    assert t030.count_tool_runs(t030_stack.dbname) == 1

    # A new key is a new run intention: the same artifact revision can be run
    # again (for example after a transient failure). Only the same key replays.
    rerun = t030.create_tool_run(owner, artifact_id)
    assert rerun.status == 202, rerun.text
    assert rerun.json["id"] != job_id
    assert t030.count_tool_runs(t030_stack.dbname) == 2

    # The same key with a different body is a conflict and creates nothing.
    other_artifact = t030.upload_ready_artifact(t030_stack, owner, t030.example_csv())
    conflict = t030.create_tool_run(owner, other_artifact, key=key)
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "idempotency_conflict"
    assert t030.count_tool_runs(t030_stack.dbname) == 2

    missing_key = owner.client.post(
        "/api/v1/tool-runs",
        body={"tool": t030.TOOL, "artifact_id": artifact_id},
        csrf=owner.csrf,
    )
    assert missing_key.status == 400, missing_key.text
    assert missing_key.json["error"]["code"] == "idempotency_key_required"

    unknown_tool = t030.create_tool_run(owner, artifact_id, tool="shell")
    assert unknown_tool.status == 422, unknown_tool.text
    assert unknown_tool.json["error"]["details"]["field"] == "tool"

    malformed = t030.create_tool_run(owner, "not-a-uuid")
    assert malformed.status == 404, malformed.text
    assert malformed.json["error"]["code"] == "resource_not_found"

    # Another user's artifact is invisible, not merely forbidden.
    for response in (
        t030.create_tool_run(other, artifact_id),
        t030.create_tool_run(other, t030.new_uuid()),
    ):
        assert response.status == 404, response.text
        assert response.json["error"]["code"] == "resource_not_found"
    assert t030.count_tool_runs(t030_stack.dbname) == 2

    # A text/plain artifact is the caller's and ready, but the preset tool does
    # not accept its media type: 422 with a located field, never a job.
    text_artifact = t030.upload_ready_artifact(t030_stack, owner, b"hello\n", t030.TEXT)
    unsupported = t030.create_tool_run(owner, text_artifact)
    assert unsupported.status == 422, unsupported.text
    assert unsupported.json["error"]["code"] == "validation_failed"
    assert unsupported.json["error"]["details"]["field"] == "artifact_id"
    assert t030.count_tool_runs(t030_stack.dbname) == 2

    # A completed-but-not-yet-finalized upload is 409 artifact_not_ready.
    pending = t030.upload_pending_artifact(t030_stack, owner, t030.example_csv())
    not_ready = t030.create_tool_run(owner, pending)
    assert not_ready.status == 409, not_ready.text
    assert not_ready.json["error"]["code"] == "artifact_not_ready"
    assert t030.count_tool_runs(t030_stack.dbname) == 2

    # Unauthenticated create/read are 401.
    anonymous = t030_stack.client()
    anonymous_create = anonymous.post(
        "/api/v1/tool-runs",
        body={"tool": t030.TOOL, "artifact_id": artifact_id},
        headers={"Idempotency-Key": t030.new_uuid()},
    )
    assert anonymous_create.status == 401, anonymous_create.text
    assert anonymous.get(f"/api/v1/tool-runs/{job_id}").status == 401


def test_api_key_scopes_are_enforced_before_the_artifact_acl(t030_stack) -> None:
    owner = user(t030_stack)
    admin = t030.admin_session(t030_stack)
    service_account = t030.create_service_account(admin, "t030 tool runner", owner.principal_id)
    _write_key_id, write_secret = t030.create_api_key(admin, service_account, ["application:write"])
    _read_key_id, read_secret = t030.create_api_key(admin, service_account, ["application:read"])

    from support import f02a

    writer = f02a.bearer_client(t030_stack, write_secret)
    reader = f02a.bearer_client(t030_stack, read_secret)
    content = t030.example_csv()
    created = writer.post(
        "/api/v1/artifacts/uploads",
        body={"media_type": t030.CSV, "size_bytes": len(content), "purpose": "task_input"},
    )
    assert created.status == 201, created.text
    artifact_id = created.json["artifact_id"]
    assert t030.put_link(created.json["upload_url"], content).status == 204
    assert writer.post(
        f"/api/v1/artifacts/{artifact_id}/complete", body={"sha256": t030.sha256_hex(content)}
    ).status == 202
    t028.finalize_once(t030_stack)
    f00b.wait_until(
        lambda: reader.get(f"/api/v1/artifacts/{artifact_id}").json.get("state") == "ready",
        what="the service account's artifact to become ready",
    )

    run = writer.post(
        "/api/v1/tool-runs",
        body={"tool": t030.TOOL, "artifact_id": artifact_id},
        headers={"Idempotency-Key": t030.new_uuid()},
    )
    assert run.status == 202, run.text
    job_id = run.json["id"]

    # A read-only key may not create; a write-only key may not read. Scope is
    # checked before the owner/ACL decision, so both are 403, not 404.
    denied_create = reader.post(
        "/api/v1/tool-runs",
        body={"tool": t030.TOOL, "artifact_id": artifact_id},
        headers={"Idempotency-Key": t030.new_uuid()},
    )
    assert denied_create.status == 403, denied_create.text
    assert denied_create.json["error"]["code"] == "insufficient_scope"
    denied_read = writer.get(f"/api/v1/tool-runs/{job_id}")
    assert denied_read.status == 403, denied_read.text
    assert denied_read.json["error"]["code"] == "insufficient_scope"

    readable = reader.get(f"/api/v1/tool-runs/{job_id}")
    assert readable.status == 200, readable.text
    t030.assert_run_view(readable.json)
    assert readable.json["id"] == job_id


def test_run_view_never_exposes_object_keys_or_staging_facts(t030_stack) -> None:
    owner = user(t030_stack)
    artifact_id = t030.upload_ready_artifact(t030_stack, owner, t030.example_csv())
    created = t030.create_tool_run(owner, artifact_id)
    assert created.status == 202
    t030.run_tool_pools_once(t030_stack)
    run = t030.wait_for_run_state(owner, created.json["id"], {"succeeded"})
    body = json.dumps(run, ensure_ascii=False)
    assert "object_key" not in body
    assert "staging" not in body
    assert "sealed/" not in body
    assert str(t030_stack.object_dir) not in body
    assert t030_stack.signing_key not in body
