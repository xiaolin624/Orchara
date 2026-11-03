"""T037: the internal build binding's capacity and lease rules.

The public endpoints are covered in the submit/review tests. This file pins the
non-contract internal surface: only the maintenance mTLS capacity may resolve a
tool_build binding, and it must prove the live attempt token of the job in the
path. Worker/supervisor certificates are authenticated but refused 403.
"""

from __future__ import annotations

import pytest

from support import f00b, t037
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)


def creator(stack, admin, login: str):
    return f00b.invite_and_activate(
        stack, admin, login, roles=("user", "creator"), password=f00b.USER_PASSWORD
    )


def internal_client(stack, role: str) -> f00b.InternalClient:
    return f00b.InternalClient(
        stack.certificates.internal_url,
        stack.certificates.ca.cert,
        stack.certificates.client(role),
    )


def test_tool_build_binding_is_maintenance_only_and_requires_the_live_token(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.binding")
    artifact_id = t037.upload_source_on(t033_stack, owner, t037.source_zip())
    tool = t037.submit_tool(owner, t037.submit_body(artifact_id)).json
    job = t037.tool_jobs(t033_stack.dbname, tool["id"])[0]
    path = f"/internal/v1/background/jobs/{job['id']}/tool-build"

    for role in ("supervisor", "worker"):
        refused = internal_client(t033_stack, role).post(
            path, {"attempt_token": t037.new_uuid()}
        )
        assert refused.status == 403, (role, refused.text)
        assert refused.json["error"]["code"] == "forbidden", refused.json

    maintenance = internal_client(t033_stack, "maintenance")
    stale = maintenance.post(path, {"attempt_token": t037.new_uuid()})
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "stale_attempt", stale.json

    claimed = maintenance.post(
        "/internal/v1/background/jobs/claim",
        {"worker_id": "t037-binding", "supported_kinds": ["tool_build"]},
    )
    assert claimed.status == 200, claimed.text
    lease = claimed.json
    assert lease["job_id"] == job["id"], lease
    assert lease["target_id"] == tool["id"], lease

    binding = maintenance.post(path, {"attempt_token": lease["attempt_token"]})
    assert binding.status == 200, binding.text
    assert binding.json["tool_version_id"] == tool["id"]
    assert binding.json["source_artifact_id"] == artifact_id
    assert binding.json["source_sha256"] == t037.tool_row(t033_stack.dbname, tool["id"])["source_sha256"]
    assert binding.json["manifest_sha256"] == t037.tool_row(t033_stack.dbname, tool["id"])["manifest_sha256"]
    # The lock is the stored canonical object, not an object key or a path.
    assert binding.json["dependency_lock"] == t037.DEFAULT_LOCK

    # Claiming moved the row to building; a worker cannot claim this job.
    worker_claim = internal_client(t033_stack, "worker").post(
        "/internal/v1/background/jobs/claim",
        {"worker_id": "t037-worker", "supported_kinds": ["tool_build"]},
    )
    assert worker_claim.status == 403, worker_claim.text
