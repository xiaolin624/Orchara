"""T057 slice 3: a real tool timeout is retried to exhaustion and explained.

The failure examples in the main smoke are business rejections: they must fail on
the first attempt. The other half of the retry boundary is a transient failure:
``tool_timeout`` is in the ``tool_run`` retry table, so the platform retries it at
most ``MaxAttempts`` (3) and then reports a terminal failure with a diagnostics
document that says ``retryable: true``.

This test exercises that path for real by restarting the maintenance process with
``--tool-timeout 1ms`` (a documented deployment knob) and a 50 ms retry backoff,
then submitting the documented sales input. Nothing is mocked: the job really is
claimed, the Python subprocess is really killed, the retries really happen, and
the diagnostics artifact is read back through the public API.
"""

from __future__ import annotations

import json
import secrets

import smoke
from support import f00b


def test_a_real_tool_timeout_is_retried_to_exhaustion_and_explained(dev_stack) -> None:
    base_url = dev_stack.base_url
    admin = smoke.sign_in(base_url, dev_stack.admin_login, dev_stack.admin_password)
    creator = smoke.invite_and_activate(
        base_url,
        admin,
        "f15a-timeout-" + secrets.token_hex(4),
        "t057-timeout-" + secrets.token_urlsafe(18),
        roles=("creator",),
    )
    content = (smoke.EXAMPLES / "input.csv").read_bytes()
    uploaded = smoke.upload_artifact(creator, content, media_type="text/csv")

    # Upload finalizing still ran on the normal maintenance process; the tool
    # execution itself runs under the 1 ms timeout so the timeout is deterministic.
    dev_stack.stop_maintenance()
    try:
        dev_stack.start_maintenance(
            "--tool-timeout",
            "1ms",
            "--poll-interval",
            "50ms",
            "--worker-id",
            "f15a-timeout-check",
            extra_env={"ORCHARA_BACKGROUND_RETRY_BACKOFF": "50ms"},
        )
        created = smoke.create_tool_run(creator, uploaded["artifact_id"])
        assert created.status == 202, created.text[:500]
        job_id = created.json["id"]
        view = smoke.wait_tool_run(creator, job_id, timeout=120)
        assert view["state"] == "failed", view
        assert view["error_code"] == "tool_timeout", view

        diagnostics = [
            artifact for artifact in view["artifacts"] if artifact["purpose"] == "diagnostics"
        ]
        assert diagnostics, view
        latest = diagnostics[-1]
        smoke.wait_for_artifact_ready(creator, latest["id"])
        document = json.loads(smoke.download_artifact(creator, latest["id"]).decode("utf-8"))
        assert document["status"] == "rejected", document
        assert document["error_code"] == "tool_timeout", document
        assert document["retryable"] is True, "a timeout is a retryable infrastructure failure"
        assert document["message"] and document["fix"], document

        job = f00b.job_row(dev_stack.dbname, job_id)
        assert job["kind"] == "tool_run", job
        assert job["state"] == "failed", job
        assert job["error_code"] == "tool_timeout", job
        assert job["failure_class"] == "infrastructure", job
        assert job["attempt_count"] == 3, f"the retry ceiling (MaxAttempts=3) was not respected: {job}"

        log = dev_stack.maintenance_log()
        assert '"result":"retried"' in log, log[-4000:]
        assert '"result":"failed"' in log, log[-4000:]
    finally:
        dev_stack.stop_maintenance()
        dev_stack.start_maintenance()
