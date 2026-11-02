"""T030 real-stack: the failure/retry/recovery boundary of the preset tool.

Three real failure classes, each with a real maintenance process:

* ``tool_timeout`` — the subprocess is killed at the configured per-run timeout.
  It is an infrastructure failure and is retried at most three times; after the
  third attempt the job is failed with ``failure_class=infrastructure``.
* ``transient_storage_io`` — the sealed input bytes disappear after the job was
  created. Reading them cannot succeed on this attempt, but retrying is the
  honest classification; the third attempt fails the job, and no result artifact
  is ever recorded.
* ``source_deleted`` — the owner-artifact is tombstoned after the job exists. A
  deleted input is a business failure (no retry, no fabricated result).

The timeout is injected with the documented deployment knob
``ORCHARA_MAINTENANCE_TOOL_TIMEOUT``, not by patching code.
"""

from __future__ import annotations

import secrets
import time

from support import f00b, t028, t030
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t030_stack,
)


def owner_session(stack: t030.T030Stack) -> t030.Session:
    admin = t030.admin_session(stack)
    return t030.invite_and_activate(stack, admin, f"t030-fail-{secrets.token_hex(3)}")


def run_until_terminal(
    stack: t030.T030Stack,
    job_id: str,
    *,
    extra_env: dict[str, str] | None = None,
    attempts: int = 6,
) -> list[dict]:
    """Run one --once drain at a time until the job is terminal.

    A retry uses the deployment backoff; this helper sets a deterministic 1s
    backoff and waits it out, so a retry is observed as a job that returned to
    ready rather than a second run racing the first drain. A process may still
    claim the retry itself; the assertions are about the durable state sequence,
    not the process boundary.
    """
    environment = {"ORCHARA_BACKGROUND_RETRY_BACKOFF": "1s"}
    environment.update(extra_env or {})
    states: list[dict] = []
    for _index in range(attempts):
        t030.run_tool_pools_once(stack, extra_env=environment)
        row = t030.job_row(stack.dbname, job_id)
        states.append(row)
        if row["state"] in {"failed", "succeeded", "cancelled"}:
            return states
        time.sleep(1.2)
    return states


def test_a_tool_timeout_is_retried_then_failed_as_infrastructure(t030_stack) -> None:
    owner = owner_session(t030_stack)
    artifact_id = t030.upload_ready_artifact(t030_stack, owner, t030.example_csv())
    created = t030.create_tool_run(owner, artifact_id)
    assert created.status == 202, created.text
    job_id = created.json["id"]

    states = run_until_terminal(
        t030_stack, job_id, extra_env={"ORCHARA_MAINTENANCE_TOOL_TIMEOUT": "1ms"}
    )
    assert states[-1]["state"] == "failed", states
    assert any(
        row["state"] in {"ready", "leased"} and row["error_code"] == "tool_timeout"
        for row in states[:-1]
    ), states
    assert any(row["failure_class"] == "infrastructure" for row in states[:-1]), states

    row = t030.job_row(t030_stack.dbname, job_id)
    assert row["state"] == "failed"
    assert row["error_code"] == "tool_timeout"
    assert row["failure_class"] == "infrastructure"
    assert int(row["attempt_count"]) == 3
    assert row["result_artifact_id"] is None

    run = t030.wait_for_run_state(owner, job_id, {"failed"})
    assert run["error_code"] == "tool_timeout"
    diagnostics = [
        artifact
        for artifact in t030.output_artifacts(t030_stack.dbname, job_id)
        if artifact["purpose"] == "diagnostics"
    ]
    assert diagnostics, "a timed-out run must leave a safe diagnostics artifact"
    last = diagnostics[-1]
    f00b.wait_until(
        lambda: t028.get_artifact(owner, last["id"]).json.get("state") == "ready",
        what="the timeout diagnostics artifact to become ready",
    )
    document = t030.read_artifact_json(owner, last["id"])
    t030.assert_diagnostics_safe(document, expected_code="tool_timeout")
    assert document["retryable"] is True
    assert "timeout" in (document["message"] + document["fix"]).lower() or "超时" in (
        document["message"] + document["fix"]
    )


def test_vanished_input_bytes_are_retried_then_failed_without_a_result(t030_stack) -> None:
    owner = owner_session(t030_stack)
    artifact_id = t030.upload_ready_artifact(t030_stack, owner, t030.example_csv())
    created = t030.create_tool_run(owner, artifact_id)
    assert created.status == 202, created.text
    job_id = created.json["id"]
    # The row still says ready/sha256; only the sealed bytes are gone. That is
    # exactly the "storage was momentarily unavailable" shape the retry table
    # exists for.
    t030.delete_sealed_object(t030_stack, artifact_id)

    states = run_until_terminal(t030_stack, job_id)
    assert states[-1]["state"] == "failed", states
    assert any(
        row["state"] in {"ready", "leased"} and row["error_code"] == "transient_storage_io"
        for row in states[:-1]
    ), states
    row = t030.job_row(t030_stack.dbname, job_id)
    assert row["error_code"] == "transient_storage_io"
    assert row["failure_class"] == "infrastructure"
    assert int(row["attempt_count"]) == 3
    assert row["result_artifact_id"] is None
    assert t030.output_artifacts(t030_stack.dbname, job_id) == []

    run = t030.wait_for_run_state(owner, job_id, {"failed"})
    t030.assert_run_view(run)
    assert run["error_code"] == "transient_storage_io"
    assert run["artifacts"] == []


def test_a_tombstoned_input_is_a_business_failure_with_no_retry(t030_stack) -> None:
    owner = owner_session(t030_stack)
    artifact_id = t030.upload_ready_artifact(t030_stack, owner, t030.example_csv())
    created = t030.create_tool_run(owner, artifact_id)
    assert created.status == 202, created.text
    job_id = created.json["id"]
    t028.tombstone_artifact(t030_stack.dbname, artifact_id)

    reports = t030.job_reports(t030.run_tool_pools_once(t030_stack)[1])
    assert len(reports) == 1, reports
    assert reports[0]["result"] == "failed", reports
    row = t030.job_row(t030_stack.dbname, job_id)
    assert row["state"] == "failed"
    assert row["error_code"] == "source_deleted"
    assert row["failure_class"] == "business"
    assert int(row["attempt_count"]) == 1
    assert row["result_artifact_id"] is None

    run = t030.wait_for_run_state(owner, job_id, {"failed"})
    assert run["error_code"] == "source_deleted"
    assert run["artifacts"] == []
