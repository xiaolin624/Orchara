"""F02B/T070 real-stack helpers: revocation, the runnable guard and history.

Everything here drives the real platform: the compiled API over real HTTP, real
PostgreSQL rows, the sealed object store (real bytes on disk) and the real
compiler (through the creator's publication-preview endpoint). A published
version is seeded from compiler-sealed canonical bytes instead of re-running the
five-case evaluation T068 already covers; the versions, review rows, published
snapshot and published run rows are schema-legal rows of the real tables.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from support import f00b, t025, t026, t027, t029, t052, t068

# The contract's terminal Run states (0014 runs.state CHECK).
TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled"}
NON_TERMINAL_STATES = ("queued", "running", "waiting_approval", "waiting_children", "cancelling")


# ---------------------------------------------------------------------------
# identities and applications
# ---------------------------------------------------------------------------
def admin_session(stack):
    return t068.admin_session(stack)


def creator(stack, *, login: str | None = None):
    return t068.creator(stack, login=login)


def sales_owner(stack, *, login: str | None = None) -> t029.Owner:
    """A genuine start -> python(sales_stats) -> end application and its owner."""
    return t068.sales_application(stack, login=login)


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
def revoke_path(version_id: str) -> str:
    return f"/api/v1/versions/{version_id}/revoke"


def runnable_path(version_id: str) -> str:
    return f"/api/v1/versions/{version_id}/runnable"


def versions_page_path(application_id: str) -> str:
    return f"/apps/{application_id}/versions"


def revoke_version(
    session,
    version_id: str,
    *,
    reason: str = "the published version must not be executed",
    stop_existing: bool = False,
    body: dict | None = None,
    omit_stop: bool = False,
    csrf: bool = True,
    extra_headers: dict[str, str] | None = None,
):
    payload = body
    if payload is None:
        payload = {"reason": reason}
        if not omit_stop:
            payload["stop_existing"] = stop_existing
    return session.client.post(
        revoke_path(version_id),
        body=payload,
        csrf=session.csrf if csrf else None,
        headers=extra_headers,
    )


def runnable_version(session, version_id: str):
    return session.client.get(runnable_path(version_id))


# ---------------------------------------------------------------------------
# the real compiler's sealed manifest (no evaluation, no sandbox)
# ---------------------------------------------------------------------------
def sealed_manifest(stack, session, application_id: str) -> dict:
    """Compile the current draft through the real preview API and return its bytes.

    The response names the artifact the compiler sealed; this helper re-reads the
    object from disk and proves the recorded artifact hash is the bytes' hash.
    """
    response = t068.preview_publication(session, application_id)
    assert response.status == 200, response.text
    content = response.json
    artifact = t068.artifact_row(stack.dbname, content["manifest_artifact_id"])
    content_bytes = (Path(stack.object_dir) / artifact["object_key"]).read_bytes()
    digest = hashlib.sha256(content_bytes).hexdigest()
    assert digest == content["manifest_sha256"], (digest, content["manifest_sha256"])
    assert digest == artifact["sha256"], (digest, artifact["sha256"])
    manifest = json.loads(content_bytes.decode("utf-8"))
    return {
        "artifact_id": content["manifest_artifact_id"],
        "object_key": artifact["object_key"],
        "manifest": manifest,
        "manifest_sha256": content["manifest_sha256"],
        "manifest_bytes": content_bytes,
        "compiler_version": manifest["compiler_version"],
        "worker_image_digest": manifest["worker_image_digest"],
        "state_schema_version": manifest["state_schema_version"],
    }


def save_capability_card(session, application_id: str, *, purpose: str) -> None:
    """Change executable content (the capability card) so a second manifest differs."""
    path = f"/api/v1/applications/{application_id}/draft"
    current = session.client.get(path)
    assert current.status == 200, current.text
    draft = current.json
    card = dict(draft.get("capability_card") or {})
    card["purpose"] = purpose
    saved = session.client.request(
        "PUT",
        path,
        body={
            "definition": draft["definition"],
            "layout": draft["layout"],
            "capability_card": card,
        },
        csrf=session.csrf,
        headers={"If-Match": current.header("ETag")},
    )
    assert saved.status == 200, saved.text


# ---------------------------------------------------------------------------
# seeded published rows
# ---------------------------------------------------------------------------
def _jsonb(value) -> str:
    return t025._literal(json.dumps(value, ensure_ascii=False)) + "::jsonb"


def seed_version(
    stack,
    *,
    owner_id: str,
    application_id: str,
    sealed: dict,
    version_no: int = 1,
) -> dict:
    """Seed one published version backed by the compiler-sealed manifest artifact."""
    dbname = stack.dbname
    review_id = f00b.new_uuid()
    version_id = f00b.new_uuid()
    report_id = f00b.new_uuid()
    report_artifact = t025.insert_artifact(dbname, owner_id, purpose="report", sha256=None)
    t025.sql_utf8(
        dbname,
        "INSERT INTO platform.evaluation_reports "
        "(id, manifest_sha256, dataset_sha256, result_artifact_id, status) VALUES ("
        f"{t025._literal(report_id)}::uuid, "
        f"{t025._literal(sealed['manifest_sha256'])}, "
        f"{t025._literal(hashlib.sha256(('dataset-' + report_id).encode()).hexdigest())}, "
        f"{t025._literal(report_artifact)}::uuid, 'passed')",
    )
    t025.sql_utf8(
        dbname,
        "INSERT INTO platform.publication_reviews "
        "(id, application_id, submitted_by, snapshot, manifest_sha256, report_id, state, "
        " reviewer_id, decided_at, manifest_artifact_id) VALUES ("
        f"{t025._literal(review_id)}::uuid, {t025._literal(application_id)}::uuid, "
        f"{t025._literal(owner_id)}::uuid, "
        + _jsonb({"seeded_by": "t070", "manifest_sha256": sealed["manifest_sha256"]})
        + ", "
        f"{t025._literal(sealed['manifest_sha256'])}, {t025._literal(report_id)}::uuid, "
        f"'approved', {t025._literal(owner_id)}::uuid, now(), "
        f"{t025._literal(sealed['artifact_id'])}::uuid)",
    )
    t025.sql_utf8(
        dbname,
        "INSERT INTO platform.application_versions "
        "(id, application_id, version_no, review_id, manifest, manifest_sha256, "
        " manifest_artifact_id, capability_card, compiler_version, worker_image_digest, "
        " state_schema_version) VALUES ("
        f"{t025._literal(version_id)}::uuid, {t025._literal(application_id)}::uuid, "
        f"{version_no}, {t025._literal(review_id)}::uuid, "
        + _jsonb(sealed["manifest"])
        + ", "
        f"{t025._literal(sealed['manifest_sha256'])}, "
        f"{t025._literal(sealed['artifact_id'])}::uuid, "
        + _jsonb(sealed["manifest"].get("capability_card") or {})
        + ", "
        f"{t025._literal(sealed['compiler_version'])}, "
        f"{t025._literal(sealed['worker_image_digest'])}, "
        f"{t025._literal(sealed['state_schema_version'])})",
    )
    return {
        "id": version_id,
        "application_id": application_id,
        "version_no": version_no,
        "review_id": review_id,
        "manifest": sealed["manifest"],
        "manifest_sha256": sealed["manifest_sha256"],
        "manifest_artifact_id": sealed["artifact_id"],
        "manifest_bytes": sealed["manifest_bytes"],
    }


def activate_version(dbname: str, application_id: str, version_id: str) -> None:
    t025.sql_utf8(
        dbname,
        "UPDATE platform.applications SET active_version_id = "
        f"{t025._literal(version_id)}::uuid "
        f"WHERE id = {t025._literal(application_id)}::uuid",
    )


def seal_bytes_artifact(stack, owner_id: str, *, purpose: str, content: bytes) -> str:
    """Write real bytes under the object store's sealed/ layout and insert the row."""
    name = os.urandom(32).hex()
    path = Path(stack.object_dir) / "sealed" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    artifact_id = f00b.new_uuid()
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision) "
        "VALUES ("
        f"{t025._literal(artifact_id)}::uuid, {t025._literal(owner_id)}::uuid, "
        f"{t025._literal('sealed/' + name)}, {t025._literal(purpose)}, "
        f"{t025._literal(hashlib.sha256(content).hexdigest())}, 'application/json', "
        f"{len(content)}, 'ready', 1)",
    )
    return artifact_id


def seed_published_run(
    stack,
    *,
    owner_id: str,
    version: dict,
    state: str = "running",
    input_bytes: bytes = b'{"rows":[]}',
) -> dict:
    """Seed a schema-legal published execution snapshot and Run for one version.

    T074 (#75) owns the real published-run creation path. Until it exists, the
    only way to assert "historical runs keep their fixed reference" is a seeded
    ``published`` snapshot/run row (0014's CHECKs make it identical in shape to a
    real one); the bytes of every artifact are real and their hashes re-verified.
    """
    dbname = stack.dbname
    application_id = version["application_id"]
    snapshot_id = f00b.new_uuid()
    run_id = f00b.new_uuid()
    input_artifact = seal_bytes_artifact(
        stack, owner_id, purpose="run_input", content=input_bytes
    )
    terminal = state in TERMINAL_RUN_STATES
    t025.sql_utf8(
        dbname,
        "INSERT INTO platform.execution_snapshots "
        "(id, kind, application_id, application_version_id, draft_revision, owner_id, manifest, "
        " manifest_sha256, manifest_artifact_id, compiler_version, worker_image_digest, "
        " state_schema_version) VALUES ("
        f"{t025._literal(snapshot_id)}::uuid, 'published', "
        f"{t025._literal(application_id)}::uuid, {t025._literal(version['id'])}::uuid, NULL, "
        f"{t025._literal(owner_id)}::uuid, "
        + _jsonb(version["manifest"])
        + ", "
        f"{t025._literal(version['manifest_sha256'])}, "
        f"{t025._literal(version['manifest_artifact_id'])}::uuid, "
        f"{t025._literal(version['manifest']['compiler_version'])}, "
        f"{t025._literal(version['manifest']['worker_image_digest'])}, "
        f"{t025._literal(version['manifest']['state_schema_version'])})",
    )
    t025.sql_utf8(
        dbname,
        "INSERT INTO platform.runs "
        "(id, request_id, conversation_id, parent_run_id, parent_invocation_key, owner_id, kind, "
        " execution_snapshot_id, application_version_id, manifest_sha256, input_artifact_id, "
        " output_artifact_id, checkpoint_thread_id, state, revision, next_event_seq, "
        " budget_limit, currency, phase, active_elapsed_ms, cancel_requested_at, error_code, "
        " started_at, finished_at, deleted_at) VALUES ("
        f"{t025._literal(run_id)}::uuid, {t025._literal(f00b.new_uuid())}::uuid, "
        f"{t025._literal(f00b.new_uuid())}::uuid, NULL, NULL, "
        f"{t025._literal(owner_id)}::uuid, 'published', "
        f"{t025._literal(snapshot_id)}::uuid, {t025._literal(version['id'])}::uuid, "
        f"{t025._literal(version['manifest_sha256'])}, {t025._literal(input_artifact)}::uuid, "
        "NULL, "
        f"{t025._literal(f00b.new_uuid())}::uuid, {t025._literal(state)}, 1, 1, 0, 'USD', "
        f"{t025._literal('idle' if terminal else 'executing')}, 0, NULL, NULL, now(), "
        f"{'now()' if terminal else 'NULL'}, NULL)",
    )
    return {
        "id": run_id,
        "application_version_id": version["id"],
        "execution_snapshot_id": snapshot_id,
        "manifest_sha256": version["manifest_sha256"],
        "input_artifact_id": input_artifact,
        "state": state,
    }


# ---------------------------------------------------------------------------
# authoritative rows
# ---------------------------------------------------------------------------
def application_row(dbname: str, application_id: str) -> dict:
    rows = t052.st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, name, "
        "active_version_id::text AS active_version_id, archived_at::text AS archived_at "
        "FROM platform.applications "
        f"WHERE id = {t052.st.quote_literal(application_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def version_row(dbname: str, version_id: str) -> dict:
    rows = t052.st.sql_rows(
        dbname,
        "SELECT id::text AS id, application_id::text AS application_id, version_no, "
        "review_id::text AS review_id, manifest::text AS manifest, manifest_sha256, "
        "manifest_artifact_id::text AS manifest_artifact_id, capability_card::text AS capability_card, "
        "compiler_version, worker_image_digest, state_schema_version, "
        "revoked_at::text AS revoked_at, revoked_by::text AS revoked_by, revoke_reason, "
        "created_at::text AS created_at FROM platform.application_versions "
        f"WHERE id = {t052.st.quote_literal(version_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def version_rows(dbname: str, application_id: str) -> list[dict]:
    return t052.st.sql_rows(
        dbname,
        "SELECT id::text AS id, application_id::text AS application_id, version_no, "
        "manifest_sha256, revoked_at::text AS revoked_at, revoked_by::text AS revoked_by, "
        "revoke_reason FROM platform.application_versions "
        f"WHERE application_id = {t052.st.quote_literal(application_id)}::uuid "
        "ORDER BY version_no",
    )


def run_row(dbname: str, run_id: str) -> dict:
    rows = t052.st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, kind, state, revision, phase, "
        "application_version_id::text AS application_version_id, "
        "execution_snapshot_id::text AS execution_snapshot_id, manifest_sha256, "
        "input_artifact_id::text AS input_artifact_id, "
        "cancel_requested_at::text AS cancel_requested_at, error_code, "
        "finished_at::text AS finished_at FROM platform.runs "
        f"WHERE id = {t052.st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def snapshot_row(dbname: str, snapshot_id: str) -> dict:
    rows = t052.st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, application_id::text AS application_id, "
        "application_version_id::text AS application_version_id, draft_revision, "
        "owner_id::text AS owner_id, manifest::text AS manifest, manifest_sha256, "
        "manifest_artifact_id::text AS manifest_artifact_id, compiler_version, "
        "worker_image_digest, state_schema_version FROM platform.execution_snapshots "
        f"WHERE id = {t052.st.quote_literal(snapshot_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def run_events(dbname: str, run_id: str) -> list[dict]:
    return t052.st.sql_rows(
        dbname,
        "SELECT seq, type, revision, payload::text AS payload FROM platform.run_events "
        f"WHERE run_id = {t052.st.quote_literal(run_id)}::uuid ORDER BY seq",
    )


def outbox_rows(dbname: str, aggregate_id: str | None = None) -> list[dict]:
    return t068.outbox_rows(dbname, aggregate_id)


def audit_rows(dbname: str, resource_id: str) -> list[dict]:
    return t052.st.sql_rows(
        dbname,
        "SELECT action, resource_type, metadata::text AS metadata FROM platform.audit_events "
        f"WHERE resource_id = {t052.st.quote_literal(resource_id)}::uuid ORDER BY created_at, id",
    )


def audit_actions(dbname: str, resource_id: str) -> list[str]:
    return [row["action"] for row in audit_rows(dbname, resource_id)]


def artifact_bytes(stack, artifact_id: str) -> bytes:
    """Read one sealed object's exact bytes and re-verify the recorded hash."""
    row = t068.artifact_row(stack.dbname, artifact_id)
    content = (Path(stack.object_dir) / row["object_key"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == row["sha256"], "the sealed bytes changed"
    return content


__all__ = [name for name in dir() if not name.startswith("_")]
