"""F11/T071 acceptance: sealed-orphan sweep deletes only provable garbage.

The real ``maintenance sweep`` command is extended by this ticket: a sealed object
is deleted only when **no live or deleted artifact row, run artifact, knowledge
chunk or memory body references it**, and only when the object is older than both
``--orphan-age`` and the documented 10-minute sealed-write grace. The grace is the
replacement for the missing writer/deletion lock: a real writer can be between
``PutSealed`` and its artifact-row commit, so a freshly written object is never an
orphan no matter what one snapshot says.

Every object in this test is a real file with a real mtime (``os.utime``); every
reference is a real row created through the real API/job path.
"""

from __future__ import annotations

from support import f00b, t069, t071


def test_the_sweep_deletes_only_provably_unreferenced_sealed_orphans(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)

    # ---- real references that must survive ---------------------------------
    _base, document, source_artifact = t071.ready_document(
        stack,
        owner.creator,
        "t071 sweep marker: referenced by a live knowledge document\n",
        log_path=tmp_path / "maintenance-t071-sweep.log",
    )
    referenced_keys = {
        t071.artifact_row(stack.dbname, source_artifact)["object_key"],
    }
    for body_artifact in t071.document_body_artifact_ids(document, stack.dbname):
        referenced_keys.add(t071.artifact_row(stack.dbname, body_artifact)["object_key"])
    memory = t071.create_memory(
        stack, owner.creator, owner.application_id, "t071 sweep marker: memory body"
    )
    referenced_keys.add(
        t071.artifact_row(
            stack.dbname, t071.memory_row(stack.dbname, memory["id"])["body_artifact_id"]
        )["object_key"]
    )
    ready_artifact = t071.ready_artifact(
        stack, owner.creator, b"t071 sweep marker: plain ready artifact\n"
    )
    referenced_keys.add(t071.artifact_row(stack.dbname, ready_artifact)["object_key"])
    # A deleted artifact's row still names its object: the deletion job owns that
    # byte removal, not the orphan sweep (it must not race the purge).
    deleted_artifact = t071.ready_artifact(
        stack, owner.creator, b"t071 sweep marker: deleted but still named\n"
    )
    deleted_key = t071.artifact_row(stack.dbname, deleted_artifact)["object_key"]
    referenced_keys.add(deleted_key)
    deleted_response = t069.delete_resource(
        owner.creator, t069.RESOURCE_ARTIFACT, deleted_artifact
    )
    assert deleted_response.status == 202, deleted_response.text
    for key in referenced_keys:
        t071.age_object(stack, key, seconds=2 * 3600)

    # A live scanning upload's staging object is never an orphan.
    live = f00b.seed_artifact(
        stack.dbname,
        stack.object_dir,
        owner.creator.principal_id,
        content=b"t071 sweep marker: staging upload still in flight",
        state="scanning",
        staging=True,
    )
    t071.age_object(stack, live.staging_key, seconds=2 * 3600)

    # ---- real garbage ------------------------------------------------------
    old_sealed = "sealed/" + f00b.random_object_name()
    f00b.write_object(stack.object_dir, old_sealed, b"t071 old sealed orphan")
    t071.age_object(stack, old_sealed, seconds=2 * 3600)
    young_sealed = "sealed/" + f00b.random_object_name()
    f00b.write_object(stack.object_dir, young_sealed, b"t071 young sealed orphan")
    old_staging = "staging/" + f00b.random_object_name()
    f00b.write_object(stack.object_dir, old_staging, b"t071 old staging orphan")
    t071.age_object(stack, old_staging, seconds=2 * 3600)

    # ---- dry run reports the same candidate set and touches nothing ---------
    completed, planned, summary = t071.sweep(stack, "--orphan-age", "1m", "--dry-run")
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert summary["status"] == "ok", summary
    assert summary["dry_run"] is True, summary
    assert summary["deleted"] == 0, summary
    planned_keys = {line["key"]: line for line in planned}
    assert old_sealed in planned_keys, planned
    assert planned_keys[old_sealed]["store"] == "sealed", planned_keys[old_sealed]
    assert planned_keys[old_sealed]["action"] == "would_delete", planned_keys[old_sealed]
    assert old_staging in planned_keys, planned
    assert young_sealed not in planned_keys, planned
    for key in referenced_keys:
        assert key not in planned_keys, (key, planned)
    assert live.staging_key not in planned_keys, planned
    assert summary["sealed_deferred"] >= 1, summary

    # ---- the real pass deletes only the proven garbage ----------------------
    completed, deleted_lines, summary = t071.sweep(stack, "--orphan-age", "1m")
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert summary["status"] == "ok", summary
    deleted = {line["key"]: line for line in deleted_lines}
    assert old_sealed in deleted, deleted_lines
    assert deleted[old_sealed]["store"] == "sealed", deleted[old_sealed]
    assert deleted[old_sealed]["action"] == "deleted", deleted[old_sealed]
    assert old_staging in deleted, deleted_lines
    assert summary["sealed_deleted"] >= 1, summary
    assert not t071.object_exists(stack, old_sealed)
    assert not t071.object_exists(stack, old_staging)

    assert t071.object_exists(stack, young_sealed), "the sealed-write grace was bypassed"
    for key in referenced_keys:
        assert t071.object_exists(stack, key), key
    assert t071.object_exists(stack, live.staging_key)

    # ---- idempotent --------------------------------------------------------
    completed, deleted_lines, summary = t071.sweep(stack, "--orphan-age", "1m")
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert deleted_lines == [], deleted_lines
    assert summary["deleted"] == 0, summary
