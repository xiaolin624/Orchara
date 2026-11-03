"""T037: submit -> building -> ready through a real runsc (gVisor) build.

The product path is exercised end to end: a real tool_source zip uploaded through
T028, ``POST /api/v1/tools`` creating the tool row plus one tool_build job, a real
``cmd/maintenance run --kinds tool_build,artifact_finalize`` process claiming it,
the checked-in ``deploy/sandbox/build_tool.py`` running inside WSL2/runsc, and
the completion guard committing ready.

Every assertion reads the real API, the real PostgreSQL rows or the real sealed
object bytes. Nothing is mocked; if WSL2/runsc is genuinely unavailable the tests
skip with the exact failing command (and the report records blocked).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from support import f00b
from support import t037
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
            "'runsc --version' failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def creator(stack, admin, login: str):
    return f00b.invite_and_activate(
        stack, admin, login, roles=("user", "creator"), password=f00b.USER_PASSWORD
    )


def submit(stack, session, source: bytes, *, body: dict | None = None) -> dict:
    artifact_id = t037.upload_source_on(stack, session, source)
    response = t037.submit_tool(session, body or t037.submit_body(artifact_id))
    assert response.status == 202, response.text
    return response.json


def test_real_runsc_build_makes_the_tool_ready_with_a_bundle_digest(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.build")
    source = t037.setup_py_source(
        "import time\n"
        "time.sleep(2)\n"
        "print('T037_BUILD_HOOK_RAN')\n"
    )
    tool = submit(t033_stack, owner, source)
    tool_id = tool["id"]

    process = t037.start_build(t033_stack)
    try:
        # The setup.py hook sleeps, so the row is observably building before the
        # build finishes. This is the contract state machine, not a transient guess.
        building = t037.wait_tool_state(t033_stack.dbname, tool_id, {"building"}, timeout=180)
        assert building["build_started_at"] is not None, building
    finally:
        reports = t037.finish_build(process)
    job_reports = f00b.job_reports(reports)
    assert job_reports, reports
    assert any(report["kind"] == "tool_build" for report in job_reports), job_reports

    facts = t037.wait_build_finished(t033_stack, owner, tool_id, timeout=120)
    assert facts["state"] == "ready", facts
    assert facts["build_error_code"] is None, facts
    assert facts["build_log_artifact_id"], facts
    assert facts["sbom_artifact_id"], facts
    assert facts["build_bundle_digest"], facts
    assert facts["build_finished_at"], facts

    row = t037.tool_row(t033_stack.dbname, tool_id)
    assert row["state"] == "ready"
    assert row["image_digest"] == row["build_bundle_digest"] == facts["build_bundle_digest"]
    assert row["build_bundle_digest"].startswith("sha256:")
    assert row["approved_at"] is None, "a build success is not an approval"
    assert int(row["revision"]) >= 2

    jobs = t037.tool_jobs(t033_stack.dbname, tool_id)
    assert len(jobs) == 1, jobs
    assert jobs[0]["state"] == "succeeded", jobs
    artifacts = t037.build_artifacts(t033_stack.dbname, jobs[0]["id"])
    assert set(artifacts) == {"sbom", "build_log"}, artifacts
    assert artifacts["sbom"]["state"] == "ready"
    assert artifacts["build_log"]["state"] == "ready"
    assert artifacts["sbom"]["id"] == row["sbom_artifact_id"]
    assert artifacts["build_log"]["id"] == row["build_log_artifact_id"]

    # The SBOM and log are owner-downloadable real bytes.
    sbom = t037.download_artifact_bytes(owner, row["sbom_artifact_id"])
    document = json.loads(sbom)
    assert document["sbom_version"] == 1
    assert document["source_sha256"] == hashlib.sha256(source).hexdigest()
    paths = {item["path"] for item in document["files"]}
    assert {"setup.py", "tool/main.py"} <= paths, paths
    assert document["dependencies"] == []

    log = t037.download_artifact_bytes(owner, row["build_log_artifact_id"])
    assert b"T037_BUILD_HOOK_RAN" in log, log
    assert b"setup.py build hook completed" in log, log

    # The recorded digest is the documented content bundle digest, computed here
    # independently from the real source/lock/SBOM bytes.
    expected = t037.bundle_digest(source, t037.DEFAULT_LOCK, sbom)
    assert row["image_digest"] == expected, (row["image_digest"], expected)


def test_the_contract_projection_carries_the_built_digest_but_not_a_fake_image(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.projection")
    tool = submit(t033_stack, owner, t037.source_zip())
    t037.run_build_once(t033_stack)
    assert t037.wait_tool_state(t033_stack.dbname, tool["id"], {"ready"}, timeout=120)

    fetched = t037.get_tool(owner, tool["id"])
    assert fetched.status == 200, fetched.text
    assert set(fetched.json) == {
        "id",
        "state",
        "approved",
        "image_digest",
        "manifest_sha256",
        "input_schema",
        "output_schema",
    }, fetched.json
    assert fetched.json["state"] == "ready"
    assert fetched.json["approved"] is False
    assert fetched.json["image_digest"].startswith("sha256:")
    # The digest describes a content bundle. This ticket has not built an OCI
    # image; F15A owns that. The value must not be presented as an image.
    assert "docker" not in fetched.json["image_digest"].lower()
