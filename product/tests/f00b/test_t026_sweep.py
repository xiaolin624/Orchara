"""T026 acceptance: the orphan sweep deletes only unreferenced staging objects.

Real files in the real object store, real artifacts rows, the real compiled
maintenance binary. The sweep must delete:

* staging objects with no live uploader (no row, or a row that already moved on:
  ready/quarantined/deleted).

It must never delete a sealed object, referenced or not. Deleting sealed bytes
needs a registration/deletion lock shared with the writers (a sealed object can be
written just before its artifact row commits), so sealed-orphan cleanup is a later
ticket (F07/F13A); this sweep reports how many unreferenced sealed objects it left
in ``sealed_deferred`` instead of guessing. It must also keep a staged object whose
artifact is still uploading/scanning. A repeated sweep is a no-op.
"""

from __future__ import annotations

from support import f00b, t026
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t026_stack,
)


def test_sweep_removes_only_orphans_and_is_idempotent(t026_stack) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    object_dir = t026_stack.object_dir

    # 1. A staging object no row references (a crashed upload).
    orphan_staging_key = "staging/" + f00b.random_object_name()
    f00b.write_object(object_dir, orphan_staging_key, b"crashed upload")
    # 2. A sealed object no row references (a crash between seal and commit).
    #    The sweep must NOT delete it: registration and deletion need a shared
    #    lock, so sealed-orphan cleanup is F07/F13A.
    orphan_sealed_key = "sealed/" + f00b.random_object_name()
    f00b.write_object(object_dir, orphan_sealed_key, b"crashed sealed bytes")

    # 3. A ready artifact: its sealed bytes are referenced and must survive.
    ready = f00b.seed_artifact(
        t026_stack.dbname,
        object_dir,
        owner,
        content=b"referenced sealed bytes",
        state="ready",
        staging=False,
        sealed_content=b"referenced sealed bytes",
    )
    # 4. A ready artifact that still has a staging leftover: no uploader is
    #    waiting for it, so the staging copy is orphaned and may go.
    leftover = f00b.seed_artifact(
        t026_stack.dbname,
        object_dir,
        owner,
        content=b"already activated bytes",
        state="ready",
        staging=True,
        sealed_content=b"already activated bytes",
    )
    # 5. A deleted artifact whose sealed bytes are still referenced: F07 owns
    #    that deletion, not the orphan sweep.
    deleted = f00b.seed_artifact(
        t026_stack.dbname,
        object_dir,
        owner,
        content=b"deleted but referenced",
        state="ready",
        staging=False,
        sealed_content=b"deleted but referenced",
    )
    f00b.delete_artifact_row(t026_stack.dbname, deleted.id)
    # 6. A live staging object for a scanning artifact: the finalize job may
    #    still need it.
    live = f00b.seed_artifact(
        t026_stack.dbname,
        object_dir,
        owner,
        content=b"waiting to be finalized",
        state="scanning",
        staging=True,
    )

    # Dry run: reports the staging candidates, deletes nothing, and already
    # reports the sealed object it will not delete.
    completed, planned, summary = t026.run_sweep(
        t026_stack, "--orphan-age", "0s", "--dry-run"
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert summary["status"] == "ok", summary
    assert summary["dry_run"] is True, summary
    assert summary["deleted"] == 0, summary
    assert summary["would_delete"] >= 2, summary
    assert summary["sealed_deferred"] == 1, summary
    assert set(t026.object_files(object_dir, "staging/")) >= {
        orphan_staging_key,
        leftover.staging_key,
        live.staging_key,
    }
    assert (object_dir / orphan_sealed_key).exists()
    assert all(line["action"] == "would_delete" for line in planned), planned
    assert all(line["store"] == "staging" for line in planned), planned

    completed, deleted_lines, summary = t026.run_sweep(t026_stack, "--orphan-age", "0s")
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert summary["status"] == "ok", summary
    # Only the staging leftovers go: the sealed orphans are preserved on purpose.
    assert summary["deleted"] == 2, (summary, deleted_lines)
    assert summary["sealed_deferred"] == 1, summary
    assert summary["deleted_bytes"] > 0

    deleted_keys = {line["key"] for line in deleted_lines}
    assert orphan_staging_key in deleted_keys, deleted_lines
    assert leftover.staging_key in deleted_keys, deleted_lines
    assert orphan_sealed_key not in deleted_keys, deleted_lines
    reasons = {line["reason"] for line in deleted_lines}
    assert "no_artifact_reference" in reasons, deleted_lines
    assert "artifact_ready" in reasons, deleted_lines
    assert all(line["action"] == "deleted" for line in deleted_lines), deleted_lines

    # What must survive: every sealed object (referenced, unreferenced and
    # tombstoned alike) and the live uploader's staging object.
    assert (object_dir / ready.object_key).exists()
    assert (object_dir / deleted.object_key).exists()
    assert (object_dir / orphan_sealed_key).exists(), "the sweep deleted sealed bytes"
    assert (object_dir / live.staging_key).exists()
    assert not (object_dir / orphan_staging_key).exists()
    assert not (object_dir / leftover.staging_key).exists()

    # Idempotent: a second sweep finds nothing to delete and still reports the
    # sealed objects it left for the later ticket.
    completed, deleted_lines, summary = t026.run_sweep(t026_stack, "--orphan-age", "0s")
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert summary["deleted"] == 0, summary
    assert summary["sealed_deferred"] == 1, summary
    assert deleted_lines == []
    assert summary["kept"] >= 3, summary


def test_sweep_refuses_to_guess_or_to_run_without_an_age(t026_stack) -> None:
    completed, _lines, _summary = t026.run_sweep(t026_stack)
    assert completed.returncode == 2, (completed.stdout, completed.stderr)
    assert "--orphan-age is required" in completed.stderr

    completed, _lines, _summary = t026.run_sweep(t026_stack, "--orphan-age", "-1s")
    assert completed.returncode == 2, (completed.stdout, completed.stderr)
