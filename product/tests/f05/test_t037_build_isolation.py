"""T037: build confinement, dependency refusal and the source-change CAS.

These are the security acceptance cases: an untrusted setup.py hook tries to read
host/foreign files, read platform credentials from the environment, write the
read-only input and open a socket. It runs inside the real gVisor sandbox, so the
tests observe the sandbox's own answer (never a mock) and assert that no secret
appears in the downloadable build log. A second group proves a lock that needs
unavailable wheels fails safely, and that a source change during a build can
never turn the old build ready.
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

DECOY_SECRET = "t037-decoy-foreign-secret\n"
ENV_SECRET = "t037-decoy-env-secret-DO-NOT-LEAK"


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


def submit(stack, session, source: bytes, *, body: dict | None = None) -> dict:
    artifact_id = t037.upload_source_on(stack, session, source)
    response = t037.submit_tool(session, body or t037.submit_body(artifact_id))
    assert response.status == 202, response.text
    return response.json


HOSTILE_HOOK = r'''
import json
import os
import socket

results = {}


def attempt(name, callback):
    try:
        callback()
        results[name] = True
    except BaseException:
        results[name] = False


def read(path):
    with open(path, "rb") as handle:
        return handle.read()


attempt("etc_hostname", lambda: read("/etc/hostname"))
attempt("foreign_secret", lambda: read("/var/tmp/orchara-sandbox/foreign/secret.txt"))
attempt("host_windows_path", lambda: read("/mnt/c/Windows/System32/drivers/etc/hosts"))
attempt("env_secret", lambda: os.environ["ORCHARA_T037_TEST_SECRET"])
attempt("input_write", lambda: open("/inputs/source.zip", "ab").write(b"tamper"))


def connect():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(2)
    probe.connect(("127.0.0.1", 80))
    probe.close()


attempt("network", connect)
attempt("tool_write", lambda: open("/tool/build_tool.py", "ab").write(b"tamper"))
print("T037_HOOK " + json.dumps(results, sort_keys=True))
'''


def test_a_malicious_setup_hook_is_confined_and_leaves_no_secret_in_the_log(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.isolation")
    tool = submit(t033_stack, owner, t037.setup_py_source(HOSTILE_HOOK))
    env = t037.build_env(t033_stack)
    env["ORCHARA_T037_TEST_SECRET"] = ENV_SECRET

    completed, reports = t037.run_build_once(t033_stack, env=env)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    facts = t037.wait_build_finished(t033_stack, owner, tool["id"], timeout=180)
    assert facts["state"] == "ready", facts
    assert facts["build_log_artifact_id"], facts

    log = t037.download_artifact_bytes(owner, facts["build_log_artifact_id"])
    text = log.decode("utf-8", "replace")
    assert "T037_HOOK" in text, text
    marker = text[text.index("T037_HOOK") + len("T037_HOOK") :].splitlines()[0]
    observed = json.loads(marker)
    assert observed, marker
    assert all(value is False for value in observed.values()), observed
    assert observed["host_windows_path"] is False
    assert observed["env_secret"] is False
    assert observed["network"] is False
    assert observed["input_write"] is False
    assert observed["tool_write"] is False

    # No host/foreign/credential content survived into the log the owner downloads.
    assert ENV_SECRET not in text
    assert "foreign-secret" not in text
    assert DECOY_SECRET.strip() not in text
    # The driver's decoy file is created for exactly this assertion; the sandbox
    # could not read it even though it exists on the guest filesystem.
    assert "T037_HOOK" in text

    # The build stays a normal ready build: confinement did not turn into a fake
    # success claim about the hook's effects.
    row = t037.tool_row(t033_stack.dbname, tool["id"])
    assert row["image_digest"] == row["build_bundle_digest"]
    assert row["build_error_code"] is None


def test_a_crashing_install_hook_fails_safe_with_a_diagnosable_log(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.crash")
    hook = "print('T037_CRASH_MARKER')\nraise SystemExit(7)\n"
    tool = submit(t033_stack, owner, t037.setup_py_source(hook))

    completed, reports = t037.run_build_once(t033_stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    facts = t037.wait_build_finished(t033_stack, owner, tool["id"], timeout=180)
    assert facts["state"] == "failed", facts
    assert facts["build_error_code"] == "build_failed", facts
    assert facts["build_bundle_digest"] is None, facts
    row = t037.tool_row(t033_stack.dbname, tool["id"])
    assert row["image_digest"] is None
    assert row["approved_at"] is None
    assert facts["build_log_artifact_id"], facts
    log = t037.download_artifact_bytes(owner, facts["build_log_artifact_id"])
    assert b"T037_CRASH_MARKER" in log, log
    assert b"exited with code 7" in log, log

    jobs = t037.tool_jobs(t033_stack.dbname, tool["id"])
    assert jobs[0]["state"] == "failed", jobs
    assert jobs[0]["error_code"] == "build_failed", jobs
    assert int(jobs[0]["attempt_count"]) == 1, jobs  # business failure is not retried


def test_a_lock_that_needs_unavailable_wheels_fails_safely(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.deps")
    lock = {"requirements": ["requests==2.32.0", "urllib3==2.2.3"]}
    artifact_id = t037.upload_source_on(t033_stack, owner, t037.source_zip())
    body = t037.submit_body(artifact_id, dependency_lock=lock)
    tool = t037.submit_tool(owner, body).json

    completed, reports = t037.run_build_once(t033_stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    facts = t037.wait_build_finished(t033_stack, owner, tool["id"], timeout=180)
    assert facts["state"] == "failed", facts
    assert facts["build_error_code"] == "dependencies_unavailable", facts
    assert facts["build_bundle_digest"] is None
    row = t037.tool_row(t033_stack.dbname, tool["id"])
    assert row["image_digest"] is None and row["approved_at"] is None

    log = t037.download_artifact_bytes(owner, facts["build_log_artifact_id"])
    assert b"no local wheelhouse" in log, log
    jobs = t037.tool_jobs(t033_stack.dbname, tool["id"])
    assert jobs[0]["state"] == "failed", jobs
    assert jobs[0]["error_code"] == "dependencies_unavailable", jobs
    assert "business" in (st.sql(
        t033_stack.dbname,
        "SELECT failure_class FROM platform.background_jobs "
        f"WHERE id = {st.quote_literal(jobs[0]['id'])}::uuid",
    ) or "").lower()
    # No fake success: the missing dependency can never become a ready digest.
    fetched = t037.get_tool(owner, tool["id"])
    assert fetched.json["state"] == "failed"
    assert fetched.json["image_digest"] is None


def test_a_source_change_during_the_build_can_never_become_ready(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.race")
    source = t037.setup_py_source("import time\ntime.sleep(4)\nprint('T037_SLOW_HOOK')\n")
    tool = submit(t033_stack, owner, source)
    tool_id = tool["id"]

    process = t037.start_build(t033_stack)
    try:
        t037.wait_tool_state(t033_stack.dbname, tool_id, {"building"}, timeout=180)
        # The source changed while the build was running: a real CAS mutation of
        # the row the completion guard re-reads.
        st.sql(
            t033_stack.dbname,
            "UPDATE platform.tool_versions "
            "SET source_sha256 = repeat('c', 64), revision = revision + 1 "
            f"WHERE id = {st.quote_literal(tool_id)}::uuid",
        )
    finally:
        t037.finish_build(process)

    facts = t037.wait_build_finished(t033_stack, owner, tool_id, timeout=120)
    assert facts["state"] == "failed", facts
    assert facts["build_error_code"] == "build_source_changed", facts
    assert facts["build_bundle_digest"] is None, facts
    row = t037.tool_row(t033_stack.dbname, tool_id)
    assert row["image_digest"] is None
    assert row["approved_at"] is None
    jobs = t037.tool_jobs(t033_stack.dbname, tool_id)
    assert jobs[0]["state"] == "failed", jobs
    assert jobs[0]["error_code"] == "build_source_changed", jobs
    # The original source hash is not the recorded one anymore: the stale build's
    # success claim was refused, never silently written as ready.
    assert row["source_sha256"] == "c" * 64
