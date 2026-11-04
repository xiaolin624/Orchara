"""T053/F15A: real local-filesystem runs of the analogue boundaries.

The platform's object store is `internal/objectstore` (local filesystem + HMAC
links). This suite builds the real Go probe against the real package, runs it in
a real directory and asserts the boundaries that *can* be proven on this machine.
It is labelled `not_s3_evidence`; the candidate-S3 acceptance stays blocked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import toolchain  # noqa: E402

PROBE_PACKAGE = "./probes/f15a_objectstore/localfs_probe"


def _go_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(toolchain.go_caches())
    return env


def _build(tmp_path: Path) -> Path:
    binary = tmp_path / ("localfs_probe.exe" if sys.platform == "win32" else "localfs_probe")
    completed = subprocess.run(
        [toolchain.go_binary(), "build", "-o", str(binary), PROBE_PACKAGE],
        cwd=PRODUCT_ROOT,
        env=_go_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    return binary


def test_local_fs_probe_runs_against_the_real_store(tmp_path: Path) -> None:
    binary = _build(tmp_path)
    root = tmp_path / "store"
    report_path = tmp_path / "local-fs.json"
    completed = subprocess.run(
        [str(binary), "-root", str(root), "-out", str(report_path)],
        cwd=PRODUCT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["scope"] == "local-filesystem"
    assert report["not_s3_evidence"] is True
    assert report["store"]["max_object_bytes"] == 20 * 1024 * 1024

    statuses = {item["id"]: item["status"] for item in report["assertions"]}
    assert statuses == {
        "key_typing_and_traversal_refused": "passed",
        "staging_sealed_separation": "passed",
        "sealed_write_boundary": "passed",
        "signed_link_boundaries": "passed",
        "delete_idempotent_and_sibling_safe": "passed",
        "size_cap_enforced": "passed",
        "anonymous_network_boundary": "not_run",
        "object_versioning": "not_run",
    }
    # Real bytes existed on disk in the real staging/sealed layout.
    assert (root / "staging").is_dir()
    assert (root / "sealed").is_dir()
    evidence = {item["id"]: item for item in report["assertions"]}
    assert evidence["staging_sealed_separation"]["evidence"]["staging_key"].startswith(
        "staging/"
    )
    assert evidence["key_typing_and_traversal_refused"]["evidence"]["valid_key_shape"]


def test_local_fs_probe_fails_closed_on_an_unusable_root(tmp_path: Path) -> None:
    binary = _build(tmp_path)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied", encoding="utf-8")
    completed = subprocess.run(
        [str(binary), "-root", str(blocker / "store")],
        cwd=PRODUCT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "cannot create probe root" in completed.stderr
