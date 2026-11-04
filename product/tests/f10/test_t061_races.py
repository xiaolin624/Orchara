"""T061 acceptance: a late index build can never activate over newer data.

Both races use real processes and real bytes, not a fake clock or a mocked guard:

1. A work-only maintenance process claims index v2's job and starts uploading the
   chunk bodies, then parks because the artifact_finalize pool is withheld. While
   it is parked, the administrator really moves the document to source revision 3
   through the reindex endpoint. Only then is the finalize pool started, so the
   old handler really finishes after the revision moved. The activation transaction
   must refuse it (`stale_revision`) and leave index v2 never activated.
2. The same parked position is used to delete the document (F07-shaped tombstone)
   before the old handler finishes. The activation transaction must refuse it
   (`source_deleted`) and must not resurrect the document; nothing ever becomes
   readable again.
"""

from __future__ import annotations

import time

from support import f00b, t055, t061, t064
from support.fixtures import t055_stack  # noqa: F401 - pytest fixture

CONTENT_V2 = (
    "# 销售资料（第二版，将被更晚版本取代）\n"
    "\n"
    "BETA-MARKER-1111：这是第二个源版本的正文，旧索引任务不能激活它。\n"
    "\n"
    "退款政策在第二版中仍然是三十天。\n"
)
CONTENT_V3 = (
    "# 销售资料（第三版，最终激活）\n"
    "\n"
    "GAMMA-MARKER-2222：这是第三个源版本的正文，最终应该成为唯一可读版本。\n"
    "\n"
    "退款政策在第三版中仍然是三十天。\n"
)


def wait_for_job_state(
    stack, document_id: str, index_version_id: str, states: set[str], *, timeout: float = 90.0
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = t061.job_for_index(stack.dbname, document_id, index_version_id)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job for index {index_version_id} never reached {states}: {last}")


def test_a_parked_old_job_cannot_activate_after_the_source_revision_moved(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifact_v1, document_id = t061.setup_ready_document(
        stack, log_name="race-stale"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    assert v1 is not None
    artifact_v2 = t055.prepare_source(stack, owner, CONTENT_V2)
    artifact_v3 = t055.prepare_source(stack, owner, CONTENT_V3)

    with t061.maintenance_pools(
        stack,
        stack.certificates,
        kinds=t061.KIND_DOCUMENT_INGEST,
        log_path=stack.server.log_path.parent / "maintenance-t061-race-work.log",
        work_slots=1,
        system_io_slots=1,
    ):
        first = t061.reindex(owner, document_id, source_artifact_id=artifact_v2)
        assert first.status == 202, first.text
        index_v2_id = first.json["index_version_id"]
        parked = t061.wait_for_parked_handler(
            stack.dbname, owner.principal_id, timeout=90.0
        )
        assert parked >= 1
        # The old job is mid-flight at source revision 2. Move the document to a
        # newer source revision through the real user path before it can finish.
        second = t061.reindex(owner, document_id, source_artifact_id=artifact_v3)
        assert second.status == 202, second.text
        assert int(second.json["revision"]) == 3
        index_v3_id = second.json["index_version_id"]
        assert t055.document_row(stack.dbname, document_id)["state"] in {"scanning", "indexing"}

        # Release the finalize pool: the parked handler stages its chunks and
        # submits success; the guard must refuse it against revision 3.
        with t061.maintenance_pools(
            stack,
            stack.certificates,
            kinds=t061.KIND_ARTIFACT_FINALIZE,
            log_path=stack.server.log_path.parent / "maintenance-t061-race-finalize.log",
            work_slots=1,
            system_io_slots=2,
        ):
            view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"}, timeout=120.0)
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))

    # The late job really ran and really produced chunks for its index version...
    late_chunks = t061.chunk_rows_for_index(stack.dbname, index_v2_id)
    assert late_chunks, "the parked handler never staged its chunk set"
    # ...but it never activated: no activated_at, failed with the stable code.
    stale_index = t061.index_by_version(stack.dbname, document_id, 2)
    assert stale_index["state"] == "failed", stale_index
    assert stale_index["error_code"] == "stale_revision", stale_index
    assert stale_index["activated_at"] is None, stale_index
    stale_job = wait_for_job_state(stack, document_id, index_v2_id, {"failed"})
    assert stale_job["state"] == "failed"
    assert stale_job["error_code"] == "stale_revision", stale_job
    assert stale_job["failure_class"] == "business", stale_job

    # The newer revision activated instead, exactly once.
    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "ready"
    assert int(row["revision"]) == 3
    assert int(row["active_revision"]) == 3
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and active["id"] == index_v3_id
    assert active["state"] == "ready"
    assert t061.index_by_version(stack.dbname, document_id, 1)["state"] == "superseded"

    # Retrieval is the newest revision only: no BETA body, no v2 chunk.
    retrieved = t055.retrieval_query(owner, base_id, "销售资料", top_k=20)
    assert retrieved.status == 200, retrieved.text
    assert retrieved.json["items"], retrieved.text
    v3_chunk_ids = {
        chunk["id"] for chunk in t061.chunk_rows_for_index(stack.dbname, index_v3_id)
    }
    returned = {item["chunk_id"] for item in retrieved.json["items"]}
    assert returned <= v3_chunk_ids
    assert "BETA-MARKER-1111" not in retrieved.text
    assert "GAMMA-MARKER-2222" in retrieved.text


def test_a_deleted_document_is_never_resurrected_by_a_parked_old_job(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifact_v1, document_id = t061.setup_ready_document(
        stack, log_name="race-delete"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    assert v1 is not None
    artifact_v2 = t055.prepare_source(stack, owner, CONTENT_V2)

    with t061.maintenance_pools(
        stack,
        stack.certificates,
        kinds=t061.KIND_DOCUMENT_INGEST,
        log_path=stack.server.log_path.parent / "maintenance-t061-delete-work.log",
        work_slots=1,
        system_io_slots=1,
    ):
        accepted = t061.reindex(owner, document_id, source_artifact_id=artifact_v2)
        assert accepted.status == 202, accepted.text
        index_v2_id = accepted.json["index_version_id"]
        t061.wait_for_parked_handler(stack.dbname, owner.principal_id, timeout=90.0)
        # F07-shaped revocation while the old job is parked mid-flight.
        t064.revoke_document(stack.dbname, document_id)
        revoked = t055.document_row(stack.dbname, document_id)
        assert revoked["state"] == "deleted"
        assert revoked["deleted_at"] is not None

        with t061.maintenance_pools(
            stack,
            stack.certificates,
            kinds=t061.KIND_ARTIFACT_FINALIZE,
            log_path=stack.server.log_path.parent / "maintenance-t061-delete-finalize.log",
            work_slots=1,
            system_io_slots=2,
        ):
            late_job = wait_for_job_state(stack, document_id, index_v2_id, {"failed"})
    assert late_job["state"] == "failed"
    assert late_job["error_code"] == "source_deleted", late_job

    # The old handler really staged its work, but the activation transaction
    # refused it: the index version never activated and the document is still
    # deleted (no resurrection).
    assert t061.chunk_rows_for_index(stack.dbname, index_v2_id)
    late_index = t061.index_by_version(stack.dbname, document_id, 2)
    assert late_index["state"] == "failed", late_index
    assert late_index["error_code"] == "source_deleted", late_index
    assert late_index["activated_at"] is None, late_index
    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "deleted", row
    assert row["deleted_at"] is not None
    assert int(row["revision"]) == 2

    # Nothing is readable again, and the tombstoned document cannot be rebuilt.
    assert t055.get_document(owner, document_id).status == 404
    assert t055.retrieval_query(owner, base_id, "销售资料").json["items"] == []
    for chunk in t061.chunk_rows_for_index(stack.dbname, index_v2_id):
        assert t055.get_citation(owner, chunk["id"]).status == 404
    refused = t061.reindex(owner, document_id)
    assert refused.status == 404, refused.text
    assert refused.json["error"]["code"] == "resource_not_found"
