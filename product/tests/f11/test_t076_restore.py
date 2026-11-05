"""F11/T076 (#77): a restored backup copy is closed again by the ledger replay.

OPERATIONS.md §46 says a restore must (1) land in an isolated environment, (2) verify
DB/object consistency, (3) replay the independent deletion ledger *before reads
open*. This suite demonstrates that control on the same real database:

* the sealed bytes are copied back from a test-owned backup file — the object store
  alone is never readable while the row stays tombstoned (every predicate re-reads
  the row, not the object);
* a naive database restore revives the row without replaying the ledger. The
  platform cannot tell a restored row from a new one, so that window is real; the
  documented procedure closes it with the replay. The test asserts both halves
  honestly instead of claiming a restore can be detected, which it cannot;
* the real ``orchara-admin deletion replay`` re-applies the tombstone, every read
  refuses again, and a second replay appends nothing (the ledger sequence count is
  the oracle, never a sleep).

The boundary is stated in the report: the platform does not own backup media, a
vendor's stores or a copy the user already downloaded. The ledger is the control for
what it can name.
"""

from __future__ import annotations

from support import t028, t069, t071, t072, t076
from support import stack as st


def test_restored_bytes_are_inert_and_the_ledger_replay_closes_a_revived_row(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    content = b"t076 restore marker: the bytes exist only in the backup copy\n"
    artifact_id = t071.ready_artifact(stack, owner.creator, content)
    before = t071.artifact_row(stack.dbname, artifact_id)
    object_key = before["object_key"]
    # A test-owned copy of the sealed bytes: the "backup medium" a restore brings
    # back. Nothing is copied from git or from another suite.
    backup = tmp_path / "t076-sealed-backup.bin"
    backup.write_bytes(t076.read_object(stack, object_key))

    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    )
    ledger_export = tmp_path / "t076-ledger.jsonl"
    exported, export_report = t072.export_ledger(stack, ledger_export)
    assert export_report["entries"] >= 1, export_report
    entries = t072.entries_for_job(stack.dbname, job["id"])
    assert [entry["entry_kind"] for entry in entries] == ["tombstone"], entries

    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports
    assert not t071.object_exists(stack, object_key)
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )

    # Restore only the object bytes (the tombstoned row is untouched): the object
    # store is not a read authority, so the content stays unreadable.
    t028.write_object(stack.object_dir, object_key, backup.read_bytes())
    assert t071.object_exists(stack, object_key)
    for suffix in ("", "/download", "/content"):
        st.assert_error(
            owner.creator.client.get(
                t069.resource_path(t069.RESOURCE_ARTIFACT, artifact_id) + suffix
            ),
            404,
            t069.ERROR_RESOURCE_NOT_FOUND,
        )
    assert t071.artifact_row(stack.dbname, artifact_id)["deleted_at"] is not None

    # A naive database restore (no ledger replay) revives the row. The platform
    # cannot distinguish it from a new row: this is exactly the window the
    # documented restore procedure closes before opening reads.
    st.sql(
        stack.dbname,
        "UPDATE platform.artifacts SET deleted_at = NULL, state = 'ready', "
        "purged_at = NULL, revision = revision - 1 "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    naive = t028.get_artifact(owner.creator, artifact_id)
    assert naive.status == 200, (naive.status, naive.text)

    # The real control: the independently retained ledger is replayed.
    completed, replay = t072.replay_ledger(stack.binaries["admin"], stack.dsn, ledger_export)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert replay["replayed"] >= 1, replay
    closed = t071.artifact_row(stack.dbname, artifact_id)
    assert closed["deleted_at"] is not None, closed
    assert closed["state"] == "deleted", closed
    assert int(closed["revision"]) > int(before["revision"]) - 1
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    st.assert_error(
        t028.read_content(owner.creator, artifact_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    # The bytes are still on disk (a backup copy is not the platform's to erase),
    # but no read path serves them: the row is the authority.
    assert t071.object_exists(stack, object_key)

    # A second replay finds every decision already applied and appends nothing.
    ledger_rows_after_first = t072.ledger_count(stack.dbname)
    completed, replay = t072.replay_ledger(stack.binaries["admin"], stack.dsn, ledger_export)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert replay["replayed"] == 0, replay
    assert replay["already_deleted"] >= 1, replay
    assert t072.ledger_count(stack.dbname) == ledger_rows_after_first
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
