"""F11/T076 (#77) real-stack helpers for the no-resurrection regression suite.

Everything in this module drives a real entry point: the compiled ``cmd/admin``
deletion-alert command, the compiled ``cmd/maintenance`` job controller, the real
HTTP API and the real filesystem object store. Only T069/T071/T072/T073 helpers are
reused; nothing here re-implements a deletion predicate.

Vocabulary mirrored from the product:

* ``source_deleted`` -- the background guard's refusal for a tombstoned input
  (``internal/background``);
* ``input_deleted`` -- the F03/T038 recovery refusal (``internal/execution``);
* ``delete_timeout`` -- the T071 retryable purge attempt timeout;
* the alert codes the F11/T076 alert surface reports for cleanup jobs that pass
  their deadline.
"""

from __future__ import annotations

import json
from pathlib import Path

from support import f00b, t028, t069, t071
from support import stack as st

# Stable safe codes the no-resurrection suite asserts.
CODE_SOURCE_DELETED = "source_deleted"
CODE_INPUT_DELETED = "input_deleted"
CODE_STALE_REVISION = "stale_revision"
CODE_DELETE_TIMEOUT = "delete_timeout"
CODE_OBJECT_STORE_UNAVAILABLE = "object_store_unavailable"
CODE_RESOURCE_NOT_FOUND = "resource_not_found"

# The F11/T076 alert surface: one machine-readable JSON line on stdout, exit 0
# when nothing is overdue and this exit code when there is at least one alert.
ALERTS_EXIT_ALERT = 3
ALERTS_EXIT_USAGE = 2

ALERT_JOB_OVERDUE = "deletion_job_overdue"
ALERT_JOB_FAILED = "deletion_job_failed"
ALERT_CLEANUP_TIMEOUT = "cleanup_timeout"
ALERT_CLEANUP_STALLED = "cleanup_stalled"
ALERT_CLEANUP_FAILED = "cleanup_failed"

# OPERATIONS.md §36: the physical-cleanup target after a logical delete is 24h.
DEFAULT_DEADLINE = "24h"

KIND_ARTIFACT_FINALIZE = "artifact_finalize"
KIND_DELETE_CONTENT = "delete_content"
KIND_DOCUMENT_INGEST = "document_ingest"


# ---------------------------------------------------------------------------
# the real object store
# ---------------------------------------------------------------------------
def sealed_keys(stack) -> set[str]:
    return t028.object_keys(stack.object_dir, "sealed/")


def store_keys(stack) -> set[str]:
    """Every key the filesystem object store holds (staging + sealed)."""
    return t028.object_keys(stack.object_dir, "staging/") | sealed_keys(stack)


def read_object(stack, key: str) -> bytes:
    return t071.read_object(stack, key)


# ---------------------------------------------------------------------------
# a late background writer (the real operator enqueue)
# ---------------------------------------------------------------------------
def enqueue_late_finalize(stack, *, artifact: dict, owner_id: str, suffix: str) -> dict:
    """Replay the finalize job of an artifact after the artifact was deleted.

    ``artifact`` is the authoritative row captured *before* the delete, so the
    replayed job carries the revision and hash the original writer was bound to --
    exactly what a duplicated/backed-up queue entry would carry.
    """
    return f00b.enqueue_ok(
        stack,
        kind=KIND_ARTIFACT_FINALIZE,
        target_id=artifact["id"],
        owner_id=owner_id,
        dedup_key=f"f11-t076-late-finalize:{artifact['id']}:{suffix}",
        expected_revision=int(artifact["revision"]),
        expected_sha256=artifact["expected_sha256"],
        input_artifact_id=artifact["id"],
    )


def job_failure(dbname: str, job_id: str) -> dict:
    return f00b.job_row(dbname, job_id)


def background_jobs(dbname: str, *, kind: str, target_id: str | None = None) -> list[dict]:
    clause = " WHERE kind = " + st.quote_literal(kind)
    if target_id is not None:
        clause += " AND target_id = " + st.quote_literal(target_id) + "::uuid"
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, target_id::text AS target_id, state, error_code, "
        "failure_class, attempt_count, dedup_key, finished_at::text AS finished_at "
        "FROM platform.background_jobs" + clause + " ORDER BY created_at, id",
    )


# ---------------------------------------------------------------------------
# the purge attempt bound (real maintenance flag, tiny deadline)
# ---------------------------------------------------------------------------
def purge_attempt_with_deadline(stack, deadline: str):
    """One real ``delete_content`` drain with an explicit attempt deadline."""
    return f00b.maintenance_once(
        stack, "--kinds", KIND_DELETE_CONTENT, "--delete-content-timeout", deadline
    )


# ---------------------------------------------------------------------------
# the deletion alert surface
# ---------------------------------------------------------------------------
def run_alerts(stack, *args: str):
    return st.run_admin(
        stack.binaries["admin"], ["deletion", "alerts", *args], dsn=stack.dsn
    )


def alert_report(completed) -> dict:
    """The one JSON line the alert command prints (stdout or stderr)."""
    for stream in (completed.stdout, completed.stderr):
        for line in stream.splitlines():
            stripped = line.strip()
            if stripped.startswith("{"):
                return json.loads(stripped)
    raise AssertionError(
        f"the alert command printed no JSON report:\n{completed.stdout}\n{completed.stderr}"
    )


def alert_codes(report: dict) -> set[str]:
    return {alert["code"] for alert in report.get("alerts", [])}


# ---------------------------------------------------------------------------
# deterministic ageing (the alert surface reads the database clock)
# ---------------------------------------------------------------------------
def age_deletion_job(dbname: str, deletion_id: str, *, hours: int) -> None:
    st.sql(
        dbname,
        "UPDATE platform.deletion_jobs "
        f"SET created_at = now() - interval '{int(hours)} hours', "
        f"started_at = COALESCE(started_at, now() - interval '{int(hours)} hours') "
        "WHERE id = " + st.quote_literal(deletion_id) + "::uuid",
    )


def age_background_job(dbname: str, job_id: str, *, hours: int) -> None:
    st.sql(
        dbname,
        "UPDATE platform.background_jobs "
        f"SET created_at = now() - interval '{int(hours)} hours', "
        f"available_at = now() - interval '{int(hours)} hours' "
        "WHERE id = " + st.quote_literal(job_id) + "::uuid",
    )


# ---------------------------------------------------------------------------
# run inputs (the T027/T074 input links)
# ---------------------------------------------------------------------------
def linked_input_artifacts(dbname: str, run_id: str) -> list[str]:
    return [
        row["artifact_id"]
        for row in st.sql_rows(
            dbname,
            "SELECT artifact_id::text AS artifact_id FROM platform.run_artifacts "
            f"WHERE run_id = {st.quote_literal(run_id)}::uuid AND relation = 'input' "
            "ORDER BY artifact_id",
        )
    ]


def link_extra_input(dbname: str, run_id: str, artifact_id: str) -> None:
    """Write the input link the real request path writes.

    ``internal/requests/submit.go`` links every request input (the sealed message
    plus each attachment) with ``relation='input'``; this helper writes the same
    row so the test can prove recovery re-checks *every* input, not only
    ``runs.input_artifact_id``. The row is real, the FK/constraints are real.
    """
    st.sql(
        dbname,
        "INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation) "
        f"VALUES ({st.quote_literal(run_id)}::uuid, {st.quote_literal(artifact_id)}::uuid, "
        "NULL, 'input')",
    )


# ---------------------------------------------------------------------------
# evidence helpers
# ---------------------------------------------------------------------------
def no_ready_row(dbname: str, artifact_id: str) -> dict:
    row = t069.artifact_row(dbname, artifact_id)
    assert row["state"] != "ready", row
    return row


def deletion_jobs_for(dbname: str, resource_id: str) -> list[dict]:
    return t069.deletion_jobs(dbname, resource_id=resource_id)


def log_contains(path: Path | None, needle: str) -> bool:
    if path is None or not path.exists():
        return False
    return needle in path.read_text(encoding="utf-8", errors="replace")
