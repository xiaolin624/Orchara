"""F11/T072: the independently retainable, replayable deletion ledger.

The ledger is the restore-time control: after a backup is restored, replaying it
must re-apply every logical deletion decision before reads open again
(OPERATIONS.md §46, DATA-MODEL.md §62, DATA-POLICY.md §15). These tests drive the
real compiled ``orchara-admin`` export/replay commands over a real PostgreSQL 16
database and prove the hash chain refuses tampering.

The hash chain is checked by the product's own verifier (export/replay refuse a
broken chain), and the replay target is a *second* fresh database migrated by the
compiled ``cmd/admin`` — a restored target, not the source database.
"""

from __future__ import annotations

import json
import uuid

from support import t069, t072
from support import stack as st


def test_a_delete_appends_one_hash_chained_tombstone_decision(t069_stack, tmp_path) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    body = "T072-LEDGER-SENTINEL " + uuid.uuid4().hex
    artifact = t069.upload_ready_artifact(stack, owner.creator, body)
    before = t069.artifact_row(stack.dbname, artifact)

    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact)
    )
    # A repeated DELETE reuses the job and must not append a second decision.
    repeated = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact)
    )
    assert repeated["id"] == job["id"], (job, repeated)

    rows = t072.ledger_rows(stack.dbname)
    assert len(rows) == 1, rows
    entry = rows[0]
    assert entry["entry_kind"] == "tombstone", entry
    assert entry["deletion_job_id"] == job["id"], entry
    assert entry["resource_type"] == "artifact"
    assert entry["resource_id"] == artifact
    assert entry["deletion_revision"] == before["revision"] + 1, entry
    assert entry["prev_sha256"] is None, entry
    assert len(entry["record_sha256"]) == 64, entry

    record = t072.ledger_record(entry)
    assert record["resource_type"] == "artifact", record
    assert record["resource_id"] == artifact, record
    # The ledger carries ids/hashes/sizes only, never the body.
    assert body not in json.dumps(record), record
    assert body not in json.dumps(rows), "the ledger leaked the deleted body"

    exported, summary = t072.export_ledger(stack, tmp_path / "ledger.jsonl")
    assert summary["status"] == "ok", summary
    assert summary["entries"] == 1, summary
    assert summary["head_sha256"] == entry["record_sha256"], summary
    header, entries = t072.ledger_file_entries(tmp_path / "ledger.jsonl")
    assert header["format"] == t072.LEDGER_FORMAT, header
    assert header["count"] == 1 and header["head_sha256"] == entry["record_sha256"], header
    assert entries[0]["entry_kind"] == "tombstone", entries[0]
    assert body not in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")


def test_replay_reapplies_the_same_decisions_on_a_fresh_restored_database(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"restore me")
    artifact_row = t069.artifact_row(stack.dbname, artifact)
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact)
    )

    exported, _ = t072.export_ledger(stack, tmp_path / "restore-ledger.jsonl")
    del exported

    # A second database is the "restored" target: same rows, pre-deletion state.
    restored = t072.restored_database(stack.binaries["admin"])
    try:
        conversation = str(uuid.uuid4())
        t072.seed_restored_principal(restored.name, artifact_row["owner_id"])
        t072.seed_restored_rows(
            restored.name,
            owner_id=artifact_row["owner_id"],
            artifact_id=artifact,
            object_key=artifact_row["object_key"],
            conversation_id=conversation,
            artifact_revision=artifact_row["revision"],
        )
        before = t072.restored_artifact_row(restored.name, artifact)
        assert before["state"] == "ready" and before["deleted_at"] is None, before
        assert before["revision"] == artifact_row["revision"], before

        completed, summary = t072.replay_ledger(
            stack.binaries["admin"],
            restored.dsn,
            tmp_path / "restore-ledger.jsonl",
        )
        assert summary["status"] == "ok", summary
        assert summary["entries"] == 1, summary
        assert summary["replayed"] == 1, summary
        assert summary["already_deleted"] == 0, summary

        after = t072.restored_artifact_row(restored.name, artifact)
        assert after["state"] == "deleted", after
        assert after["deleted_at"] is not None, after
        assert after["revision"] == before["revision"] + 1, after

        # The replayed decision is recorded in the restored database's own ledger,
        # and a second replay appends nothing and re-deletes nothing.
        replay_entries = t072.ledger_rows(restored.name)
        assert len(replay_entries) == 1, replay_entries
        replay_record = t072.ledger_record(replay_entries[0])
        assert replay_record["source"] == "replay", replay_record
        assert replay_record["original_sequence"] == 1, replay_record
        assert replay_entries[0]["deletion_job_id"] == job["id"], replay_entries

        completed_again, again = t072.replay_ledger(
            stack.binaries["admin"],
            restored.dsn,
            tmp_path / "restore-ledger.jsonl",
        )
        assert again["replayed"] == 0, again
        assert again["already_deleted"] == 1, again
        assert t072.ledger_count(restored.name) == 1
    finally:
        st.drop_database(restored.name)


def test_a_tampered_ledger_file_is_refused_and_changes_nothing(t069_stack, tmp_path) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"tamper target")
    artifact_row = t069.artifact_row(stack.dbname, artifact)
    t069.deletion_of(t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact))
    t072.export_ledger(stack, tmp_path / "tamper.jsonl")

    header, entries = t072.ledger_file_entries(tmp_path / "tamper.jsonl")
    # Flip one record field but keep the recorded hashes: the chain no longer
    # verifies and replay must refuse before touching the target.
    entries[0]["record"]["resource_id"] = str(uuid.uuid4())
    t072.write_ledger_file(tmp_path / "tampered.jsonl", header, entries)

    restored = t072.restored_database(stack.binaries["admin"])
    try:
        t072.seed_restored_principal(restored.name, artifact_row["owner_id"])
        t072.seed_restored_rows(
            restored.name,
            owner_id=artifact_row["owner_id"],
            artifact_id=artifact,
            object_key=artifact_row["object_key"],
            conversation_id=str(uuid.uuid4()),
            artifact_revision=artifact_row["revision"],
        )
        refused, _ = t072.replay_ledger(
            stack.binaries["admin"],
            restored.dsn,
            tmp_path / "tampered.jsonl",
            expect_ok=False,
        )
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert "ledger" in (refused.stderr + refused.stdout).lower(), refused.stderr
        assert t072.ledger_count(restored.name) == 0
        unchanged = t072.restored_artifact_row(restored.name, artifact)
        assert unchanged["deleted_at"] is None and unchanged["state"] == "ready", unchanged

        # --check-only performs the same verification and changes nothing.
        checked, _ = t072.replay_ledger(
            stack.binaries["admin"],
            restored.dsn,
            tmp_path / "tampered.jsonl",
            check_only=True,
            expect_ok=False,
        )
        assert checked.returncode == 1, checked.stdout + checked.stderr
        assert t072.ledger_count(restored.name) == 0

        # Removing a middle entry (a truncated/edited backup) is refused too.
        header, entries = t072.ledger_file_entries(tmp_path / "tamper.jsonl")
        t072.write_ledger_file(tmp_path / "truncated.jsonl", header, entries[:0])
        empty, _ = t072.replay_ledger(
            stack.binaries["admin"],
            restored.dsn,
            tmp_path / "truncated.jsonl",
            check_only=True,
            expect_ok=False,
        )
        assert empty.returncode == 1, empty.stdout + empty.stderr
    finally:
        st.drop_database(restored.name)


def test_replay_restores_the_derived_document_and_conversation_tombstones(
    t069_stack, tmp_path
) -> None:
    """One artifact decision carries its derived document; a conversation is its
    own decision. Both must be reproduced by a replay on a fresh database."""
    stack = t069_stack
    owner = t069.owner(stack)
    base, document = t069.ready_document(
        stack, owner.creator, b"restored document body", log_path=tmp_path / "ingest.log"
    )
    source = t069.document_row(stack.dbname, document)["source_artifact_id"]
    source_row = t069.artifact_row(stack.dbname, source)
    document_row = t069.document_row(stack.dbname, document)
    assert document_row["state"] == "ready", document_row

    conversation = str(uuid.uuid4())
    st.sql(
        stack.dbname,
        "INSERT INTO platform.conversations (id, owner_id, revision) VALUES ("
        f"'{conversation}'::uuid, '{source_row['owner_id']}'::uuid, 1)",
    )

    t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, source)
    )
    t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_CONVERSATION, conversation)
    )
    tombstones = [
        entry
        for entry in t072.ledger_rows(stack.dbname)
        if entry["entry_kind"] == "tombstone"
    ]
    assert len(tombstones) == 2, tombstones
    artifact_record = next(
        t072.ledger_record(entry) for entry in tombstones if entry["resource_type"] == "artifact"
    )
    assert artifact_record["derived_document_ids"] == [document], artifact_record

    export_path = tmp_path / "derived-ledger.jsonl"
    t072.export_ledger(stack, export_path)
    restored = t072.restored_database(stack.binaries["admin"])
    try:
        t072.seed_restored_principal(restored.name, source_row["owner_id"])
        t072.seed_restored_rows(
            restored.name,
            owner_id=source_row["owner_id"],
            artifact_id=source,
            object_key=source_row["object_key"],
            conversation_id=conversation,
            artifact_revision=source_row["revision"],
        )
        t072.seed_restored_document(
            restored.name,
            owner_id=source_row["owner_id"],
            source_artifact_id=source,
            knowledge_base_id=base,
            document_id=document,
            revision=document_row["revision"],
        )
        _, summary = t072.replay_ledger(stack.binaries["admin"], restored.dsn, export_path)
        assert summary["replayed"] == 2, summary

        artifact_after = t072.restored_artifact_row(restored.name, source)
        assert artifact_after["state"] == "deleted" and artifact_after["deleted_at"] is not None
        document_after = t072.restored_document_row(restored.name, document)
        assert document_after["state"] == "deleted", document_after
        assert document_after["deleted_at"] is not None, document_after
        conversation_after = t072.restored_conversation_row(restored.name, conversation)
        assert conversation_after["deleted_at"] is not None, conversation_after
        # Already tombstoned rows are never resurrected or re-deleted.
        _, again = t072.replay_ledger(stack.binaries["admin"], restored.dsn, export_path)
        assert again["replayed"] == 0 and again["already_deleted"] == 2, again
    finally:
        st.drop_database(restored.name)


def test_the_ledger_table_is_append_only(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"append only")
    t069.deletion_of(t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact))

    update = st.psql(
        stack.dbname,
        "UPDATE platform.deletion_ledger SET record = '{}'::jsonb WHERE sequence = 1",
    )
    assert update.code != 0, update.output
    assert "append-only" in update.output.lower(), update.output
    delete = st.psql(stack.dbname, "DELETE FROM platform.deletion_ledger WHERE sequence = 1")
    assert delete.code != 0, delete.output
    truncate = st.psql(stack.dbname, "TRUNCATE platform.deletion_ledger")
    assert truncate.code != 0, truncate.output
    assert t072.ledger_count(stack.dbname) == 1
