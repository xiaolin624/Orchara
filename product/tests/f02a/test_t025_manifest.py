"""T025: the manifest artifact — golden bytes, data-driven hash and dedupe.

Everything here goes through the real endpoint and the real object store. The
golden digest comes from ``tests/contracts/snapshots`` (the Go builder test
regenerates it and fails on drift); this test proves the HTTP path produces
exactly those bytes.
"""

from __future__ import annotations

import hashlib
import json
import secrets

from support import stack as st, t025
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t025_stack,
)


def owner(t025_ctx):
    admin = t025.admin_session(t025_ctx)
    login = "t025-manifest-" + secrets.token_hex(4)
    return admin, t025.invite_and_activate(t025_ctx, admin, login), login


def sealed_files(object_dir) -> list[str]:
    return sorted(path.name for path in (object_dir / "sealed").glob("*") if path.is_file())


def test_validate_seals_exactly_the_golden_bytes_and_hash(t025_stack) -> None:
    admin, creator, _login = owner(t025_stack)
    application_id = t025.seed_golden_draft(t025_stack.dbname, creator.principal_id)
    golden_jcs, golden_sha = t025.golden_bytes("sales")

    response = t025.validate(creator, application_id)
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.json
    assert response.header("X-Manifest-SHA256") == golden_sha, response.header("X-Manifest-SHA256")

    rows = t025.manifest_artifacts(t025_stack.dbname, application_id)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["manifest_sha256"] == golden_sha
    assert row["execution_revision"] == 1
    assert row["state"] == "ready"
    assert row["sha256"] == golden_sha
    assert row["size_bytes"] == len(golden_jcs)
    assert row["deleted_at"] is None
    assert t025.manifest_artifact_bytes(t025_stack.object_dir, row) == golden_jcs

    # The sealed bytes are the canonical manifest the worker can re-verify: hash
    # the object bytes and compare with the recorded digest and the header.
    assert hashlib.sha256(t025.manifest_artifact_bytes(t025_stack.object_dir, row)).hexdigest() == golden_sha
    manifest = json.loads(golden_jcs)
    assert manifest["application_id"] == t025.GOLDEN_APPLICATION_ID
    assert manifest["source_revision"] == 1
    assert manifest["worker_image_digest"] == t025.WORKER_IMAGE_DIGEST
    assert manifest["runtime_lock_sha256"] == t025.runtime_lock_sha256()
    assert manifest["tool_artifacts"] == [
        {"id": t025.GOLDEN_TOOL_ID, "image_digest": t025.TOOL_IMAGE_DIGEST}
    ]
    assert manifest["model_profile_versions"] == [t025.GOLDEN_MODEL_ID]
    assert manifest["connector_policies"] == []
    assert set(manifest) == {
        "manifest_version", "application_id", "source_revision", "compiler_version",
        "worker_image_digest", "runtime_lock_sha256", "state_schema_version",
        "definition", "capability_card", "tool_artifacts", "model_profile_versions",
        "subapp_versions", "connector_policies",
    }

    # A repeated validate is byte-identical: one row, one sealed object.
    files_before = sealed_files(t025_stack.object_dir)
    again = t025.validate(creator, application_id)
    assert again.status == 200, again.text
    assert again.header("X-Manifest-SHA256") == golden_sha
    assert len(t025.manifest_artifacts(t025_stack.dbname, application_id)) == 1
    assert sealed_files(t025_stack.object_dir) == files_before

    # Validate is not a publication: no version/review/job/snapshot rows exist.
    assert t025.count_where_application(t025_stack.dbname, "application_versions", application_id) == 0
    assert t025.count_where_application(t025_stack.dbname, "publication_reviews", application_id) == 0
    assert t025.count(t025_stack.dbname, "background_jobs") == 0


def test_layout_only_save_keeps_the_hash_and_definition_change_changes_it(t025_stack) -> None:
    admin, creator, _login = owner(t025_stack)
    application_id = t025.seed_golden_draft(t025_stack.dbname, creator.principal_id)
    golden_jcs, golden_sha = t025.golden_bytes("sales")

    first = t025.validate(creator, application_id)
    assert first.status == 200, first.text
    assert first.header("X-Manifest-SHA256") == golden_sha

    # A layout-only save increments the row revision (ETag) but not the
    # executable-content revision.
    draft = t025.get_draft(creator, application_id)
    new_layout = {"viewport": {"x": 42, "y": 7, "zoom": 0.5}}
    saved = t025.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=t025.golden_definition(),
        layout=new_layout,
        capability_card=t025.golden_card(),
    )
    assert saved.status == 200, saved.text
    row = t025.draft_row(t025_stack.dbname, application_id)
    assert row["revision"] == 2, row
    assert row["execution_revision"] == 1, row
    assert row["layout"] == new_layout

    layout_response = t025.validate(creator, application_id)
    assert layout_response.status == 200, layout_response.text
    assert layout_response.header("X-Manifest-SHA256") == golden_sha
    assert len(t025.manifest_artifacts(t025_stack.dbname, application_id)) == 1

    # A definition change moves execution_revision and the hash.
    changed = t025.golden_definition()
    changed["nodes"][2]["config"]["instructions"] = "依据已验证统计生成报告；不改变金额。"
    draft = t025.get_draft(creator, application_id)
    saved = t025.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=changed,
        layout=new_layout,
        capability_card=t025.golden_card(),
    )
    assert saved.status == 200, saved.text
    row = t025.draft_row(t025_stack.dbname, application_id)
    assert row["revision"] == 3, row
    assert row["execution_revision"] == 2, row

    changed_response = t025.validate(creator, application_id)
    assert changed_response.status == 200, changed_response.text
    changed_sha = changed_response.header("X-Manifest-SHA256")
    assert changed_sha and changed_sha != golden_sha
    rows = t025.manifest_artifacts(t025_stack.dbname, application_id)
    assert len(rows) == 2, rows
    by_sha = {entry["manifest_sha256"]: entry for entry in rows}
    assert set(by_sha) == {golden_sha, changed_sha}
    assert by_sha[changed_sha]["execution_revision"] == 2
    changed_manifest = json.loads(t025.manifest_artifact_bytes(t025_stack.object_dir, by_sha[changed_sha]))
    assert changed_manifest["source_revision"] == 2
    assert changed_manifest["definition"]["nodes"][2]["config"]["instructions"] == changed["nodes"][2]["config"]["instructions"]

    # Repeating the changed validation writes no third object.
    assert t025.validate(creator, application_id).header("X-Manifest-SHA256") == changed_sha
    assert len(t025.manifest_artifacts(t025_stack.dbname, application_id)) == 2
    assert len(sealed_files(t025_stack.object_dir)) == 2

    # A capability-card change is executable content too: the hash moves again.
    card = t025.golden_card()
    card["purpose"] = "summarise sales; the card is part of the execution content"
    draft = t025.get_draft(creator, application_id)
    saved = t025.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=changed,
        layout=new_layout,
        capability_card=card,
    )
    assert saved.status == 200, saved.text
    row = t025.draft_row(t025_stack.dbname, application_id)
    assert row["execution_revision"] == 3, row
    card_response = t025.validate(creator, application_id)
    assert card_response.status == 200, card_response.text
    card_sha = card_response.header("X-Manifest-SHA256")
    assert card_sha and card_sha not in {golden_sha, changed_sha}
    assert len(t025.manifest_artifacts(t025_stack.dbname, application_id)) == 3
    assert len(sealed_files(t025_stack.object_dir)) == 3


def test_tampering_with_the_sealed_bytes_breaks_the_recorded_digest(t025_stack) -> None:
    admin, creator, _login = owner(t025_stack)
    application_id = t025.seed_golden_draft(t025_stack.dbname, creator.principal_id)
    response = t025.validate(creator, application_id)
    assert response.status == 200, response.text
    recorded_sha = response.header("X-Manifest-SHA256")
    row = t025.manifest_artifacts(t025_stack.dbname, application_id)[0]
    path = t025_stack.object_dir / row["object_key"]

    # Flip one byte in the sealed object. The worker's verification mechanism is
    # compiler.VerifyManifestBytes: it hashes the bytes first and compares with
    # the recorded digest before parsing (proved end-to-end in the Go tests).
    payload = bytearray(path.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    path.write_bytes(bytes(payload))
    tampered_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert tampered_digest != recorded_sha
    assert tampered_digest != row["sha256"]
