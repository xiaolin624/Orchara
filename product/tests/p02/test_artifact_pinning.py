"""T009: after an upgrade, a run still loads its original artifact.

Two properties matter and are easy to get wrong:

* publishing a newer graph must **not** change what an in-flight run resumes
  against;
* if the pinned artifact is gone, resuming must fail with `artifact_unavailable`
  instead of quietly using the newest version.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import pg_dev  # noqa: E402

PROBE = PRODUCT_ROOT / "probes" / "p02_checkpoint" / "checkpoint_probe.py"
VENV_PYTHON = PRODUCT_ROOT / "probes" / "agent_min" / ".venv" / (
    "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
)
MIGRATIONS = [
    PRODUCT_ROOT / "db" / "migrations" / "0001_worker_lease.sql",
    PRODUCT_ROOT / "db" / "migrations" / "0002_artifacts.sql",
]


def _run(*args: str, expect_success: bool = True) -> dict:
    completed = subprocess.run(
        [str(VENV_PYTHON), str(PROBE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if expect_success and completed.returncode != 0:
        pytest.fail(f"probe {args} failed ({completed.returncode}):\n{completed.stderr}")
    assert completed.stdout.strip(), f"probe {args} produced no JSON: {completed.stderr}"
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _psql(sql: str) -> None:
    import os

    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    subprocess.run(
        [
            pg_dev._tool("psql"),
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_dev.DEFAULT_PORT),
            "-U",
            pg_dev.DEFAULT_USER,
            "-d",
            pg_dev.DEFAULT_DB,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


@pytest.fixture(scope="module", autouse=True)
def artifacts_table() -> None:
    if not VENV_PYTHON.exists():
        pytest.skip("locked probe environment is missing")
    if not pg_dev.is_running():
        pytest.skip("development cluster is not running")
    for migration in MIGRATIONS:
        _psql(migration.read_text(encoding="utf-8"))


def _version() -> int:
    # High, unique versions so each test's publish is the newest at that moment.
    return 100_000 + random.randint(1, 400_000)


def _scalar(sql: str) -> str:
    import os

    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    completed = subprocess.run(
        [
            pg_dev._tool("psql"),
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_dev.DEFAULT_PORT),
            "-U",
            pg_dev.DEFAULT_USER,
            "-d",
            pg_dev.DEFAULT_DB,
            "-tAc",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    return completed.stdout.strip()


def _next_version() -> int:
    """A version strictly newer than anything published so far.

    `run` pins whatever is newest, so a test that published an older number would
    silently test nothing.
    """
    current = _scalar("SELECT coalesce(max(graph_version), 0) FROM app.artifacts")
    return int(current) + 1


def test_a_new_publish_does_not_change_what_a_run_resumes_with() -> None:
    v1 = _next_version()
    published_v1 = _run("publish", "--version", str(v1))
    thread = f"t-{uuid.uuid4().hex[:10]}"
    started = _run("run", "--thread", thread, "--owner", "alice")
    assert started["interrupted"] is True

    # Publish an upgrade while the run is waiting for approval.
    published_v2 = _run("publish", "--version", str(v1 + 1))
    assert published_v2["graph_version"] == v1 + 1

    resumed = _run("resume", "--thread", thread, "--owner", "alice", "--decision", "approve")
    assert resumed["pinned_artifact"] == published_v1["artifact_id"]
    assert resumed["loaded_version"] == v1, (
        f"resumed on version {resumed['loaded_version']} but the run was pinned to {v1}"
    )
    assert resumed["artifact_digest"] == published_v1["digest"], (
        "the digest must come from the pinned artifact, not the newest one"
    )


def test_resuming_against_a_deleted_artifact_reports_artifact_unavailable() -> None:
    version = _next_version()
    _run("publish", "--version", str(version))
    thread = f"t-{uuid.uuid4().hex[:10]}"
    _run("run", "--thread", thread, "--owner", "alice")

    _run("delete-artifact", "--version", str(version))

    failed = _run("resume", "--thread", thread, "--owner", "alice", "--decision", "approve",
                  expect_success=False)
    assert failed["error"] == "artifact_unavailable"
    assert failed["artifact_id"] == f"artifact-v{version}"


def test_a_deleted_artifact_is_not_offered_as_the_latest() -> None:
    version = _next_version()
    _run("publish", "--version", str(version))
    newer = _run("publish", "--version", str(version + 1))
    _run("delete-artifact", "--version", str(version + 1))

    latest = _run("latest-artifact")
    assert latest["graph_version"] != newer["graph_version"]
    assert latest["graph_version"] >= version
