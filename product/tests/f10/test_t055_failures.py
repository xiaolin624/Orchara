"""T055 acceptance: empty parse, corrupt input and failure/retry boundaries.

A document may only become ready when parsing produced non-empty content and every
chunk/embedding was written. These tests prove the negative branches through the
real pipeline: the empty and invalid-UTF-8 cases end in a failed document with a
specific code (never ready), a business failure is not retried, a retryable job
failure returns to ready with an incremented attempt count and later succeeds,
and a lost lease is recovered by the next claim.
"""

from __future__ import annotations

from support import f00b, t055
from support.fixtures import t055_stack  # noqa: F401 - pytest fixture


def setup_user(stack):
    admin = f00b.admin_session(stack)
    alice = f00b.invite_and_activate(stack, admin, "t055.alice", password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, "t055 failures").json["id"]
    return alice, base_id


def ingest_with_pools(stack, alice, base_id, artifact_id, log_name: str):
    with t055.ingest_pools(stack, stack.server.log_path.parent / log_name):
        accepted = t055.ingest_document(alice, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(alice, document_id, {"ready", "failed"})
    return document_id, view


def assert_failed_document(stack, alice, base_id, document_id: str, code: str) -> None:
    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "failed", row
    assert row["error_code"] == code, row
    assert row["active_revision"] is None, row
    assert t055.chunk_count(stack.dbname, document_id) == 0
    jobs = t055.document_jobs(stack.dbname, document_id)
    assert jobs and jobs[-1]["state"] == "failed", jobs
    assert jobs[-1]["error_code"] == code, jobs
    assert jobs[-1]["failure_class"] == "business", jobs
    diagnostics = t055.document_diagnostics(alice, document_id)
    assert diagnostics.status == 200, diagnostics.text
    assert diagnostics.json["error_code"] == code
    assert diagnostics.json["chunk_count"] == 0
    assert diagnostics.json["background_job"]["state"] == "failed"
    # A failed document is not searchable: an empty answer, not an error.
    queried = t055.retrieval_query(alice, base_id, "任何内容")
    assert queried.status == 200, queried.text
    assert queried.json["items"] == []


def test_an_empty_parse_is_failed_and_never_ready(t055_stack) -> None:
    stack = t055_stack
    alice, base_id = setup_user(stack)
    artifact_id = t055.prepare_source(stack, alice, t055.EMPTY_DOCUMENT)

    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t055-empty.log"):
        accepted = t055.ingest_document(alice, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(alice, document_id, {"ready", "failed"})
        assert view["state"] == "failed", view
        assert_failed_document(stack, alice, base_id, document_id, "ingest_empty_content")

        # Re-ingesting the same artifact is an explicit retry: a new document
        # revision and a new job. It still cannot become ready, and the second
        # job is another business failure rather than an endless retry.
        again = t055.ingest_document(alice, base_id, artifact_id)
        assert again.status == 202, again.text
        assert again.json["id"] == document_id
        assert again.json["state"] == "scanning"
        retried = t055.wait_for_document_state(alice, document_id, {"failed"}, timeout=30.0)
    assert retried["revision"] == 2
    row = t055.document_row(stack.dbname, document_id)
    assert row["revision"] == 2 and row["state"] == "failed"
    assert row["error_code"] == "ingest_empty_content"
    jobs = t055.document_jobs(stack.dbname, document_id)
    assert len(jobs) == 2, jobs
    assert {job["state"] for job in jobs} == {"failed"}
    assert t055.chunk_count(stack.dbname, document_id) == 0


def test_invalid_utf8_input_is_failed_with_its_own_code(t055_stack) -> None:
    stack = t055_stack
    alice, base_id = setup_user(stack)
    # The T028 admission gate would refuse invalid UTF-8, so this seeds the real
    # ready artifact directly to exercise the parser's own refusal. The bytes and
    # row are real; only the admission step is bypassed, and the document path is
    # still the public one.
    artifact_id = t055.seed_ready_source_artifact(
        stack, alice.principal_id, t055.INVALID_UTF8_DOCUMENT
    )
    document_id, view = ingest_with_pools(
        stack, alice, base_id, artifact_id, "maintenance-t055-bad-utf8.log"
    )
    assert view["state"] == "failed", view
    assert_failed_document(stack, alice, base_id, document_id, "ingest_invalid_encoding")


def test_a_retryable_job_failure_returns_to_ready_and_then_succeeds(t055_stack) -> None:
    stack = t055_stack
    alice, base_id = setup_user(stack)
    artifact_id = t055.prepare_source(stack, alice, t055.SAMPLE_DOCUMENT)

    accepted = t055.ingest_document(alice, base_id, artifact_id)
    assert accepted.status == 202, accepted.text
    document_id = accepted.json["id"]
    jobs = t055.document_jobs(stack.dbname, document_id)
    assert len(jobs) == 1
    job_id = jobs[0]["id"]

    client = stack.internal_client()
    lease = client.claim(worker_id="t055-retry", kinds=[t055.KIND_DOCUMENT_INGEST])
    assert lease.status == 200, lease.text
    assert lease.json["job_id"] == job_id
    failed = client.result(
        job_id,
        attempt_token=lease.json["attempt_token"],
        state="failed",
        error_code="ingest_chunk_artifact_timeout",
    )
    assert failed.status == 200, failed.text
    retried = t055.document_jobs(stack.dbname, document_id)[0]
    assert retried["state"] == "ready", retried
    assert retried["failure_class"] == "infrastructure", retried
    assert int(retried["attempt_count"]) == 1
    # A retryable infrastructure failure must not fail the document.
    assert t055.document_row(stack.dbname, document_id)["state"] == "scanning"

    t055.t026.wait_for_retry_available(stack.dbname, job_id)
    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t055-retry.log"):
        view = t055.wait_for_document_state(alice, document_id, {"ready", "failed"})

    assert view["state"] == "ready", view
    job = t055.document_jobs(stack.dbname, document_id)[0]
    assert job["state"] == "succeeded", job
    assert int(job["attempt_count"]) == 2, job
    assert t055.chunk_count(stack.dbname, document_id, 1) > 0


def test_a_lost_lease_is_recovered_by_the_next_claim(t055_stack) -> None:
    stack = t055_stack
    alice, base_id = setup_user(stack)
    artifact_id = t055.prepare_source(stack, alice, t055.SAMPLE_DOCUMENT)
    accepted = t055.ingest_document(alice, base_id, artifact_id)
    document_id = accepted.json["id"]
    job_id = t055.document_jobs(stack.dbname, document_id)[0]["id"]

    client = stack.internal_client()
    lease = client.claim(worker_id="t055-lost-lease", kinds=[t055.KIND_DOCUMENT_INGEST])
    assert lease.status == 200, lease.text
    # The process disappears without a result; the lease expires and the next
    # claim must recover the same job with an incremented attempt.
    f00b.force_lease_expiry(stack.dbname, job_id)
    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t055-lease.log"):
        view = t055.wait_for_document_state(alice, document_id, {"ready", "failed"})

    assert view["state"] == "ready", (view, t055.document_jobs(stack.dbname, document_id))
    job = t055.document_jobs(stack.dbname, document_id)[0]
    assert job["state"] == "succeeded", job
    assert int(job["attempt_count"]) == 2, job
    assert len(t055.chunk_rows(stack.dbname, document_id, 1)) >= 3
