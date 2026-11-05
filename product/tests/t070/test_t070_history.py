"""T070: historical runs keep their fixed reference through a revocation.

A real seeded ``published`` execution snapshot and Run row (T074 owns the real
published-run creator) pin ``application_version_id``, ``execution_snapshot_id``
and ``manifest_sha256`` at start. Revocation is a state change on the version
row only: this test reads the version, the snapshot, the Run and the sealed
object bytes back and proves none of them moved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from support import t068, t070
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def test_historical_runs_and_sealed_bytes_are_unchanged_by_revocation(t070_stack) -> None:
    owner = t070.sales_owner(t070_stack, login="t070-history")
    admin = t070.admin_session(t070_stack)
    sealed = t070.sealed_manifest(t070_stack, owner.creator, owner.application_id)
    version = t070.seed_version(
        t070_stack, owner_id=owner.creator.principal_id,
        application_id=owner.application_id, sealed=sealed,
    )
    t070.activate_version(t070_stack.dbname, owner.application_id, version["id"])

    historical = t070.seed_published_run(
        t070_stack, owner_id=owner.creator.principal_id, version=version, state="running"
    )

    # Captured before the revocation.
    version_before = t070.version_row(t070_stack.dbname, version["id"])
    snapshot_before = t070.snapshot_row(t070_stack.dbname, historical["execution_snapshot_id"])
    run_before = t070.run_row(t070_stack.dbname, historical["id"])
    sealed_bytes_before = t070.artifact_bytes(t070_stack, version["manifest_artifact_id"])
    assert (
        hashlib.sha256(sealed_bytes_before).hexdigest() == version["manifest_sha256"]
    ), "the seeded version must reference the compiler-sealed bytes"

    revoked = t070.revoke_version(
        admin, version["id"], reason="withdraw the version from new runs",
        stop_existing=True,
    )
    assert revoked.status == 200, revoked.text

    # 1. The Run's fixed reference is unchanged; only the cancel intent is new.
    run_after = t070.run_row(t070_stack.dbname, historical["id"])
    for column in (
        "application_version_id", "execution_snapshot_id", "manifest_sha256",
        "input_artifact_id", "kind", "owner_id",
    ):
        assert run_after[column] == run_before[column], (column, run_before, run_after)
    assert run_after["cancel_requested_at"], run_after
    assert run_after["state"] == "running", run_after

    # 2. The execution snapshot is byte-for-byte the same row.
    assert t070.snapshot_row(t070_stack.dbname, historical["execution_snapshot_id"]) == snapshot_before

    # 3. The version row keeps its immutable columns; only the revoke triple moved.
    version_after = t070.version_row(t070_stack.dbname, version["id"])
    for column in (
        "manifest", "manifest_sha256", "manifest_artifact_id", "version_no",
        "review_id", "capability_card", "compiler_version", "worker_image_digest",
        "state_schema_version", "created_at",
    ):
        assert version_after[column] == version_before[column], (column, version_before, version_after)
    assert version_after["revoked_at"] and version_before["revoked_at"] is None, version_after
    assert version_after["revoke_reason"] == "withdraw the version from new runs", version_after

    # 4. The sealed bytes are read again and re-hashed: no rewrite happened.
    sealed_bytes_after = t070.artifact_bytes(t070_stack, version["manifest_artifact_id"])
    assert sealed_bytes_after == sealed_bytes_before
    assert hashlib.sha256(sealed_bytes_after).hexdigest() == version["manifest_sha256"]
    artifact = t068.artifact_row(t070_stack.dbname, version["manifest_artifact_id"])
    assert artifact["state"] == "ready" and artifact["deleted_at"] is None, artifact

    # 5. The outbox row names the same hash/version a consumer would rebuild from.
    payload = json.loads(t070.outbox_rows(t070_stack.dbname, version["id"])[0]["payload"])
    assert payload["manifest_sha256"] == version["manifest_sha256"], payload
    assert payload["version_id"] == version["id"], payload
    assert payload["previous_active_version_id"] == version["id"], payload
