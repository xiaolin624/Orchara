"""T028 acceptance: deletion and finalize never resurrect an artifact.

The delete endpoint and deletion jobs belong to F07. What T028 must prove is the
race boundary: a finalize job is in flight (a real lease was claimed), F07's
tombstone columns are written the way F07 will write them, and then the real
completion guard runs. The artifact must not become ready and must never be
readable afterwards. The attempt-object fence is the determinism technique used
by ``tests/f00b/test_t022_finalize.py``: the worker half writes only the
attempt's own fenced object, then reports success with its live token.
"""

from __future__ import annotations

import secrets

from support import t028
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t028_stack,
)


def test_a_tombstone_written_while_finalize_is_in_flight_wins(t028_stack) -> None:
    admin = t028.admin_session(t028_stack)
    owner = t028.invite_and_activate(t028_stack, admin, "t028-race-" + secrets.token_hex(3))
    content = b"the bytes a paused finalize attempt already sealed\n"

    created = t028.create_upload(
        owner, media_type=t028.TEXT, size_bytes=len(content), purpose="task_input"
    )
    assert created.status == 201, created.text
    artifact_id = created.json["artifact_id"]
    assert t028.put_link(created.json["upload_url"], content).status == 204
    assert t028.complete_upload(owner, artifact_id, t028.sha256_hex(content)).status == 202
    jobs = t028.artifact_jobs(t028_stack.dbname, artifact_id)
    assert len(jobs) == 1, jobs
    job_id = jobs[0]["id"]

    # The finalize job is in flight: a real maintenance certificate claims it.
    lease_response = t028_stack.internal_client().claim(kinds=["artifact_finalize"])
    assert lease_response.status == 200, lease_response.text
    lease = lease_response.json
    assert lease["job_id"] == job_id, lease
    assert t028.job_row(t028_stack.dbname, job_id)["state"] == "leased"

    # The handler half has written only this attempt's fenced object. The
    # artifact's active sealed key is still untouched.
    attempt_key = t028.attempt_sealed_key(job_id, lease["attempt_token"])
    t028.write_object(t028_stack.object_dir, attempt_key, content)
    before = t028.artifact_row(t028_stack.dbname, artifact_id)
    assert before["state"] == "scanning"
    assert not (t028_stack.object_dir / before["object_key"]).exists()

    # F07's tombstone propagation, written with the columns F07 will write.
    t028.tombstone_artifact(t028_stack.dbname, artifact_id)
    tombstoned = t028.artifact_row(t028_stack.dbname, artifact_id)
    assert tombstoned["state"] == "deleted"
    assert tombstoned["deleted_at"] is not None
    assert int(tombstoned["revision"]) > int(before["revision"])

    # Let the real guard run against the live lease.
    result = t028_stack.internal_client().result(
        job_id, attempt_token=lease["attempt_token"], state="succeeded"
    )
    assert result.status == 200, result.text
    assert result.json["state"] == "failed", result.json
    assert result.json["error_code"] == "source_deleted", result.json

    job = t028.job_row(t028_stack.dbname, job_id)
    assert job["state"] == "failed"
    assert job["failure_class"] == "business"
    assert job["result_artifact_id"] is None

    after = t028.artifact_row(t028_stack.dbname, artifact_id)
    assert after["state"] == "deleted"
    assert after["deleted_at"] is not None
    assert after["state"] != "ready"
    assert after["sha256"] is None
    assert not (t028_stack.object_dir / after["object_key"]).exists()
    # The guard deleted only the failed attempt's own object.
    assert not (t028_stack.object_dir / attempt_key).exists()
    assert t028.object_keys(t028_stack.object_dir, "sealed/") == set()

    # A tombstoned artifact is invisible and never readable, and cannot be
    # completed into a second job after the race.
    for response in (
        t028.get_artifact(owner, artifact_id),
        t028.download_artifact(owner, artifact_id),
        t028.read_content(owner, artifact_id),
    ):
        assert response.status == 404, (response.status, response.text)
        assert response.json["error"]["code"] == "resource_not_found"
    assert len(t028.artifact_jobs(t028_stack.dbname, artifact_id)) == 1
    retry = t028.complete_upload(owner, artifact_id, t028.sha256_hex(content))
    assert retry.status == 409, retry.text
    assert retry.json["error"]["code"] == "artifact_conflict", retry.json
    assert len(t028.artifact_jobs(t028_stack.dbname, artifact_id)) == 1
