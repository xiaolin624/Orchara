"""F11/T071 real-stack helpers: asynchronous object/index/cache purge.

Everything here drives the real product surfaces: the throwaway PostgreSQL 16
database migrated by the compiled ``cmd/admin``, the compiled ``cmd/api`` behind
HTTP, real sealed bytes in a filesystem object store, the real internal mTLS
listener and the compiled ``cmd/maintenance`` process (``run --once --kinds
delete_content`` and ``sweep``).

The helpers add three things on top of :mod:`support.t069`:

* the ``delete_content`` background job T069's delete transaction enqueues
  (``dedup_key = deletion:<deletion_job_id>``, ``target_id = deletion_job_id``);
* authoritative row reads for the physical copies (chunks, index versions,
  retrieval-cache rows) so an assertion never trusts a flag;
* the real object-store file operations: a Windows file handle held open by the
  test blocks ``os.Remove`` with a genuine sharing violation (the failure
  injection), and ``os.utime`` ages a real object for the orphan sweep.

The scope vocabulary is mirrored from ``internal/deletion``: T071 owns
``object_store``, ``search_index`` and ``retrieval_cache``; the remaining four
belong to T072.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from support import f00b, t026, t055, t058, t067, t069
from support import stack as st

KIND_DELETE_CONTENT = "delete_content"
# The four scopes T072 owns, in the stable sorted order the API projects.
T072_SCOPES = sorted(t069.T072_SCOPES)

# Mirror of cmd/maintenance/sweep.go sealedOrphanGrace: a sealed object younger
# than this is never treated as an orphan, because a writer can be between
# sealing the object and committing its artifact row.
SEALED_ORPHAN_GRACE_SECONDS = 10 * 60

ERROR_OBJECT_SHARED = "object_shared_reference"
CODE_TRANSIENT_STORAGE = "transient_storage_io"


# ---------------------------------------------------------------------------
# the delete_content background job
# ---------------------------------------------------------------------------
def dedup_key(deletion_id: str) -> str:
    return "deletion:" + deletion_id


def delete_content_jobs(
    dbname: str, *, deletion_id: str | None = None
) -> list[dict]:
    clause = f"WHERE kind = {st.quote_literal(KIND_DELETE_CONTENT)}"
    if deletion_id is not None:
        clause += f" AND target_id = {st.quote_literal(deletion_id)}::uuid"
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, target_id::text AS target_id, "
        "owner_id::text AS owner_id, dedup_key, state, error_code, failure_class, "
        "attempt_count, available_at::text AS available_at, "
        "finished_at::text AS finished_at "
        "FROM platform.background_jobs " + clause + " ORDER BY created_at, id",
    )


def delete_content_job(dbname: str, deletion_id: str) -> dict:
    rows = delete_content_jobs(dbname, deletion_id=deletion_id)
    assert len(rows) == 1, rows
    return rows[0]


def get_background_job(session, job_id: str):
    return session.client.get("/api/v1/background-jobs/" + job_id)


# ---------------------------------------------------------------------------
# authoritative rows for the physical copies
# ---------------------------------------------------------------------------
def chunk_rows(dbname: str, document_id: str) -> list[dict]:
    return t055.chunk_rows(dbname, document_id)


def index_versions(dbname: str, document_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, document_id::text AS document_id, version, "
        "document_revision, state, chunk_count "
        "FROM platform.knowledge_index_versions "
        f"WHERE document_id = {st.quote_literal(document_id)}::uuid "
        "ORDER BY version",
    )


def cache_rows(dbname: str, **kwargs) -> list[dict]:
    return t067.cache_rows(dbname, **kwargs)


def artifact_row(dbname: str, artifact_id: str) -> dict:
    return t069.artifact_row(dbname, artifact_id)


def deletion_job(dbname: str, deletion_id: str) -> dict:
    row = t069.deletion_job(dbname, deletion_id)
    assert row is not None, deletion_id
    return row


def remaining(job: dict) -> list[str]:
    return t069.remaining_scopes(job)


def completed(job: dict) -> list[str]:
    return t069.completed_scopes(job)


def document_row(dbname: str, document_id: str) -> dict:
    return t069.document_row(dbname, document_id)


def memory_row(dbname: str, memory_id: str) -> dict:
    return t069.memory_row(dbname, memory_id)


# ---------------------------------------------------------------------------
# real object-store files
# ---------------------------------------------------------------------------
def object_path(stack, key: str) -> Path:
    return stack.object_dir / key


def object_exists(stack, key: str) -> bool:
    return object_path(stack, key).exists()


def age_object(stack, key: str, *, seconds: float) -> None:
    """Give a real object file a real old mtime (no fake clock anywhere)."""
    path = object_path(stack, key)
    assert path.is_file(), path
    when = time.time() - seconds
    os.utime(path, (when, when))


def hold_open(stack, key: str):
    """Hold a real Windows file handle open so ``os.Remove`` gets a sharing
    violation. The returned handle must be closed by the caller.

    This is the failure injection used by ``test_t071_failure.py``: it is the
    filesystem refusing the deletion, not a mocked object store.
    """
    path = object_path(stack, key)
    assert path.is_file(), path
    return open(path, "rb")


def read_object(stack, key: str) -> bytes:
    return object_path(stack, key).read_bytes()


# ---------------------------------------------------------------------------
# real maintenance process
# ---------------------------------------------------------------------------
def purge_once(stack, *args: str):
    """One real ``maintenance run --once --kinds delete_content`` drain."""
    return f00b.maintenance_once(stack, "--kinds", KIND_DELETE_CONTENT, *args)


def purge_report(reports: list[dict]) -> dict:
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, reports
    return job_reports[0]


def purge_summary(reports: list[dict]) -> dict:
    return f00b.summary_of(reports)


def wait_retry_available(dbname: str, job_id: str, *, timeout: float = 30.0) -> None:
    """Wait on the database clock until a retryable job's backoff elapsed."""
    f00b.wait_until(
        lambda: st.sql(
            dbname,
            "SELECT available_at <= now() FROM platform.background_jobs "
            f"WHERE id = {st.quote_literal(job_id)}::uuid",
        )
        == "t",
        timeout=timeout,
        what=f"the retry of background job {job_id} to become available",
    )


def retry_until_claimed(
    stack, dbname: str, *, timeout: float = 60.0
) -> list[dict]:
    """Run ``purge_once`` until the short configured backoff elapsed and a job ran."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _completed, reports = purge_once(stack)
        if f00b.summary_of(reports)["claimed"] > 0:
            return reports
        time.sleep(0.02)
    raise AssertionError(f"maintenance never claimed the purge job within {timeout}s")


def sweep(stack, *args: str):
    """One real ``maintenance sweep`` pass."""
    return t026.run_sweep(stack, *args)


# ---------------------------------------------------------------------------
# scenario helpers built on the T069 real paths
# ---------------------------------------------------------------------------
def ready_artifact(stack, session, content: bytes | str) -> str:
    return t069.upload_ready_artifact(stack, session, content)


def ready_document(stack, session, content: bytes | str, *, log_path: Path):
    """One real ingested document; returns ``(base_id, document_id, source_id)``."""
    base_id, document_id = t069.ready_document(stack, session, content, log_path=log_path)
    source_id = t055.document_row(stack.dbname, document_id)["source_artifact_id"]
    return base_id, document_id, source_id


def document_object_keys(stack, document_id: str) -> list[str]:
    """Every sealed key the document's scope can name: source + chunk bodies."""
    document = t055.document_row(stack.dbname, document_id)
    keys = [artifact_row(stack.dbname, document["source_artifact_id"])["object_key"]]
    for chunk in chunk_rows(stack.dbname, document_id):
        keys.append(artifact_row(stack.dbname, chunk["body_artifact_id"])["object_key"])
    return sorted(set(keys))


def document_body_artifact_ids(document_id: str, dbname: str) -> list[str]:
    return sorted({chunk["body_artifact_id"] for chunk in chunk_rows(dbname, document_id)})


def second_knowledge_base(session, name: str) -> str:
    base = t055.create_knowledge_base(session, name)
    assert base.status == 201, base.text
    return base.json["id"]


def ingest_ready_document(stack, session, base_id: str, source_artifact_id: str, *, log_path: Path) -> str:
    """Ingest one existing ready artifact into a knowledge base (real jobs)."""
    with t055.ingest_pools(stack, log_path):
        accepted = t055.ingest_document(session, base_id, source_artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(session, document_id, {"ready", "failed"})
        assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    return document_id


def create_memory(stack, session, application_id: str, text: str) -> dict:
    return t058.create_memory_ok(session, application_id, text)


def purge_scope_counts(dbname: str) -> dict:
    """A stable, field-by-field count snapshot for idempotency assertions."""
    return {
        "chunks": int(st.sql(dbname, "SELECT count(*) FROM platform.knowledge_chunks")),
        "index_versions": int(
            st.sql(dbname, "SELECT count(*) FROM platform.knowledge_index_versions")
        ),
        "cache_rows": int(
            st.sql(dbname, "SELECT count(*) FROM platform.knowledge_retrieval_cache")
        ),
        "purged_artifacts": int(
            st.sql(dbname, "SELECT count(*) FROM platform.artifacts WHERE purged_at IS NOT NULL")
        ),
        "delete_content_jobs": len(delete_content_jobs(dbname)),
        "deletion_jobs": int(st.sql(dbname, "SELECT count(*) FROM platform.deletion_jobs")),
    }
