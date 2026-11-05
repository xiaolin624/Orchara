"""F08/T077: the incremental ``catalog_index`` background handler.

The handler is the seam a later publication/revocation hook (and the admin CLI)
calls. It re-reads the authoritative version inside its write transaction, so it
either writes one correct projection row or reconciles the row away; it never
trusts the job's request-time facts beyond the expected manifest hash.
"""

from __future__ import annotations

from support import f00b, f08, t077
from support import stack as st


def job_rows(dbname: str, version_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, state, error_code, failure_class, attempt_count, "
        "dedup_key, expected_sha256, target_id::text AS target_id "
        "FROM platform.background_jobs "
        f"WHERE kind = 'catalog_index' AND target_id = {st.quote_literal(version_id)}::uuid "
        "ORDER BY created_at, id",
    )


def test_a_catalog_index_job_updates_one_projection_entry(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    first = t077.publish_version(stack, owner.session, purpose="catalog job first")
    t077.rebuild_ok(stack, "--all")

    second = t077.publish_version(stack, owner.session, purpose="catalog job second")
    before = t077.search_ok(t077.search(owner.session, "catalog job"))
    assert before["retrieval"]["source"] == "fallback", before["retrieval"]
    assert t077.entry_rows(stack.dbname, version_id=second.version_id) == []

    enqueued = t077.index_once(stack, second.version_id)
    assert enqueued["status"] == "ok" and enqueued["created"] is True, enqueued
    assert enqueued["version_id"] == second.version_id
    pending = job_rows(stack.dbname, second.version_id)
    assert len(pending) == 1 and pending[0]["state"] == "ready", pending
    assert pending[0]["dedup_key"] == second.version_id + ":" + second.manifest_sha256
    assert pending[0]["expected_sha256"] == second.manifest_sha256

    completed, reports = t077.run_catalog_maintenance(stack)
    assert completed.returncode == 0, completed.stderr
    assert f00b.summary_of(reports)["succeeded"] == 1, reports
    succeeded = job_rows(stack.dbname, second.version_id)
    assert succeeded[0]["state"] == "succeeded", succeeded
    entry = t077.entry_rows(stack.dbname, version_id=second.version_id)
    assert len(entry) == 1, entry
    assert second.purpose in entry[0]["search_text"]
    assert entry[0]["source_manifest_sha256"] == second.manifest_sha256

    after = t077.search_ok(t077.search(owner.session, "catalog job"))
    assert after["retrieval"]["source"] == "index", after["retrieval"]
    assert {item["version_id"] for item in after["items"]} == {
        first.version_id,
        second.version_id,
    }


def test_the_handler_reconciles_a_revoked_version_before_writing(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    published = t077.publish_version(stack, owner.session, purpose="catalog job revoked")
    t077.rebuild_ok(stack, "--all")
    assert len(t077.entry_rows(stack.dbname, version_id=published.version_id)) == 1

    # The job is enqueued while the version is valid, then the version is revoked
    # before the maintenance process claims it. The handler's own authoritative
    # re-read must remove the stale entry instead of writing it again.
    t077.index_once(stack, published.version_id)
    f08.revoke_version(stack.dbname, published.version_id, owner.principal_id)

    completed, reports = t077.run_catalog_maintenance(stack)
    assert completed.returncode == 0, completed.stderr
    assert f00b.summary_of(reports)["succeeded"] == 1, reports
    rows = job_rows(stack.dbname, published.version_id)
    assert rows[0]["state"] == "succeeded", rows
    assert rows[0]["error_code"] is None, rows
    assert t077.entry_rows(stack.dbname, version_id=published.version_id) == []

    empty = t077.search_ok(t077.search(owner.session, "catalog job revoked"))
    assert empty["items"] == [], empty
    assert empty["retrieval"]["source"] == "index", empty["retrieval"]


def test_the_handler_refuses_invalid_metadata_without_writing_a_row(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    published = t077.publish_version(stack, owner.session, purpose="catalog job invalid")
    st.sql(
        stack.dbname,
        "UPDATE platform.application_versions SET capability_card = '{\"purpose\": 1}'::jsonb "
        f"WHERE id = {st.quote_literal(published.version_id)}::uuid",
    )
    t077.index_once(stack, published.version_id)

    completed, reports = t077.run_catalog_maintenance(stack)
    assert completed.returncode == 0, completed.stderr
    summary = f00b.summary_of(reports)
    assert summary["failed"] == 1, reports
    rows = job_rows(stack.dbname, published.version_id)
    assert rows[0]["state"] == "failed", rows
    assert rows[0]["error_code"] == "catalog_source_invalid", rows
    assert rows[0]["failure_class"] == "business", rows
    assert rows[0]["attempt_count"] == 1, rows
    assert t077.entry_rows(stack.dbname, version_id=published.version_id) == []

    # No projection, no ledger row, no claim of readiness: search falls back and
    # the malformed card's purpose is not leaked.
    body = t077.search_ok(t077.search(owner.session, "catalog job invalid"))
    assert body["items"] == [], body
    assert body["retrieval"]["source"] == "fallback", body["retrieval"]
    assert "catalog job invalid" not in t077.search(owner.session, "catalog job invalid").text
