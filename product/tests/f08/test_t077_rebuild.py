"""F08/T077: the catalog projection, its rebuild process and its isolation.

The rebuild is the compiled ``cmd/admin catalog rebuild`` process against the real
database. The interruption test starts that process, waits for its committed
ledger to say ``rebuilding`` while a real psql session holds an ACCESS EXCLUSIVE
lock on ``application_versions``, then terminates the process: the ledger must
still record the epoch/start and search must fall back, never half-serve. The
separation test seeds real knowledge rows and proves the catalog rebuild leaves
their counts and identities untouched.
"""

from __future__ import annotations

from support import f08, t077
from support import stack as st

CARD = {
    "purpose": "fixed catalog purpose",
    "includes": ["aggregation"],
    "excludes": ["external writes"],
    "required_inputs": ["sales csv"],
    "expected_outputs": ["report"],
    "examples": ["monthly report", "weekly digest"],
}


def test_the_catalog_rebuild_never_touches_the_knowledge_index(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    published = t077.publish_version(stack, owner.session, purpose="catalog knowledge split")
    seeded = t077.seed_knowledge_rows(stack, owner.principal_id)

    counts_before = t077.knowledge_counts(stack.dbname)
    signature_before = t077.knowledge_signature(stack.dbname)
    assert counts_before == {
        "knowledge_chunks": 1,
        "knowledge_index_versions": 1,
        "knowledge_documents": 1,
    }, counts_before

    report = t077.rebuild_ok(stack, "--all")
    assert report["indexed"] == 1, report
    assert [row["application_version_id"] for row in t077.entry_rows(stack.dbname)] == [
        published.version_id
    ]
    assert t077.knowledge_counts(stack.dbname) == counts_before
    assert t077.knowledge_signature(stack.dbname) == signature_before

    # An incremental projection write (the catalog_index handler path) is equally
    # incapable of touching the knowledge projection.
    t077.publish_version(stack, owner.session, purpose="catalog knowledge split second")
    t077.index_once(stack, published.version_id)
    completed, reports = t077.run_catalog_maintenance(stack)
    assert completed.returncode == 0, completed.stderr
    assert t077.knowledge_signature(stack.dbname) == signature_before
    assert seeded["chunk_id"] in st.sql(
        stack.dbname,
        "SELECT string_agg(id::text, ',') FROM platform.knowledge_chunks",
    )


def test_rebuild_restores_search_after_projection_truncation_and_consumes_outbox(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    first = t077.publish_version(stack, owner.session, purpose="catalog rebuild alpha")
    second = t077.publish_version(stack, owner.session, purpose="catalog rebuild beta")

    # A database that has never been indexed still answers honestly from the
    # authorized full text; it does not pretend an empty projection is complete.
    initial = t077.search_ok(t077.search(owner.session, "catalog rebuild"))
    assert initial["retrieval"]["source"] == "fallback", initial["retrieval"]
    assert initial["retrieval"]["index_state"] == "absent", initial["retrieval"]
    assert {item["version_id"] for item in initial["items"]} == {
        first.version_id,
        second.version_id,
    }
    assert t077.entry_rows(stack.dbname) == []
    assert t077.outbox_rows(stack.dbname)[0]["delivered_at"] is None

    report = t077.rebuild_ok(stack, "--all")
    assert report["state"] == "ready", report
    assert report["projection_epoch"] == 1, report
    assert report["expected"] == 2 and report["indexed"] == 2, report
    assert report["outbox_consumed"] == 2, report
    state = t077.state_row(stack.dbname)
    assert state["state"] == "ready" and int(state["projection_epoch"]) == 1, state
    assert state["rebuilt_at"] is not None, state
    assert state["last_error_code"] is None, state
    for row in t077.outbox_rows(stack.dbname):
        assert row["delivered_at"] is not None, row
        assert row["attempts"] == 1, row

    indexed = t077.search_ok(t077.search(owner.session, "catalog rebuild"))
    assert indexed["retrieval"]["source"] == "index", indexed["retrieval"]
    assert indexed["retrieval"]["stale"] is False, indexed["retrieval"]
    assert {item["version_id"] for item in indexed["items"]} == {
        first.version_id,
        second.version_id,
    }

    # The restart: truncate the projection rows, keeping the ledger. Search must
    # notice the live count mismatch, drop back to the authorized full text and
    # never serve the empty projection as complete.
    t077.truncate_projection(stack.dbname)
    degraded = t077.search_ok(t077.search(owner.session, "catalog rebuild"))
    assert degraded["retrieval"]["source"] == "fallback", degraded["retrieval"]
    assert degraded["retrieval"]["index_state"] == "ready", degraded["retrieval"]
    assert degraded["retrieval"]["stale"] is True, degraded["retrieval"]
    assert {item["version_id"] for item in degraded["items"]} == {
        first.version_id,
        second.version_id,
    }

    second_report = t077.rebuild_ok(stack, "--all")
    assert second_report["projection_epoch"] == 2, second_report
    assert second_report["indexed"] == 2, second_report
    # The outbox rows were already delivered; a rebuild consumes only pending
    # signals, it does not inflate the attempts of a delivered row.
    assert second_report["outbox_consumed"] == 0, second_report
    assert {row["attempts"] for row in t077.outbox_rows(stack.dbname)} == {1}
    restored = t077.search_ok(t077.search(owner.session, "catalog rebuild"))
    assert restored["retrieval"]["source"] == "index", restored["retrieval"]


def test_a_stale_hash_entry_is_not_served_and_rebuild_repairs_it(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    published = t077.publish_version(stack, owner.session, purpose="catalog stale hash")
    t077.rebuild_ok(stack, "--all")

    # Simulate a metadata revision the projection has not caught up with: the
    # entry's source hash no longer names the authoritative version bytes.
    st.sql(
        stack.dbname,
        "UPDATE platform.application_catalog_entries SET source_manifest_sha256 = "
        f"{st.quote_literal('0' * 64)} WHERE application_version_id = "
        f"{st.quote_literal(published.version_id)}::uuid",
    )
    stale = t077.search_ok(t077.search(owner.session, "catalog stale hash"))
    assert stale["retrieval"]["source"] == "fallback", stale["retrieval"]
    assert stale["retrieval"]["stale"] is True, stale["retrieval"]
    assert [item["version_id"] for item in stale["items"]] == [published.version_id], stale
    assert [item["manifest_sha256"] for item in stale["items"]] == [
        published.manifest_sha256
    ], stale

    repaired = t077.rebuild_ok(stack, "--all")
    assert repaired["projection_epoch"] == 2, repaired
    assert [row["source_manifest_sha256"] for row in t077.entry_rows(stack.dbname)] == [
        published.manifest_sha256
    ]
    healthy = t077.search_ok(t077.search(owner.session, "catalog stale hash"))
    assert healthy["retrieval"]["source"] == "index", healthy["retrieval"]


def test_an_interrupted_rebuild_records_the_epoch_and_never_half_serves(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    published = [
        t077.publish_version(stack, owner.session, purpose=f"catalog interrupted {index}")
        for index in range(3)
    ]
    t077.rebuild_ok(stack, "--all")
    assert t077.state_row(stack.dbname)["state"] == "ready"
    t077.truncate_projection(stack.dbname)

    lock = t077.TableLock(stack.dbname, "application_versions")
    process = None
    try:
        lock.wait_until_held()
        process = t077.start_rebuild(stack)
        rebuilding = t077.wait_until(
            lambda: (
                row
                if (row := t077.state_row(stack.dbname)) and row["state"] == "rebuilding"
                else None
            ),
            timeout=60,
        )
        assert rebuilding is not None, t077.state_row(stack.dbname)
        epoch = rebuilding["projection_epoch"]
        assert rebuilding["rebuild_started_at"] is not None, rebuilding
        process.kill()
        process.wait(timeout=30)
        assert process.returncode != 0
        interrupted = t077.state_row(stack.dbname)
        assert interrupted["state"] == "rebuilding", interrupted
        assert interrupted["projection_epoch"] == epoch, interrupted
        assert interrupted["rebuild_started_at"] is not None, interrupted
    finally:
        if process is not None and process.poll() is None:  # pragma: no cover
            process.kill()
            process.wait(timeout=15)
        lock.release()

    def _versions_readable() -> bool:
        try:
            st.sql(stack.dbname, "SELECT count(*) FROM platform.application_versions")
            return True
        except AssertionError:  # pragma: no cover - lock teardown race
            return False

    assert t077.wait_until(_versions_readable, timeout=30), "the lock was never released"

    # The killed process left the ledger rebuilding; search must fall back to the
    # authority and still return every readable version. It must never serve a
    # half-built projection as if it were complete.
    degraded = t077.search_ok(t077.search(owner.session, "catalog interrupted"))
    assert degraded["retrieval"]["source"] == "fallback", degraded["retrieval"]
    assert degraded["retrieval"]["stale"] is True, degraded["retrieval"]
    assert {item["version_id"] for item in degraded["items"]} == {
        version.version_id for version in published
    }, degraded

    # A complete rerun takes the ledger back to ready and repair the projection.
    report = t077.rebuild_ok(stack, "--all")
    assert report["indexed"] == 3, report
    healthy = t077.search_ok(t077.search(owner.session, "catalog interrupted"))
    assert healthy["retrieval"]["source"] == "index", healthy["retrieval"]
    assert {item["version_id"] for item in healthy["items"]} == {
        version.version_id for version in published
    }


def test_a_failed_rebuild_records_its_error_and_search_keeps_falling_back(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    good = t077.publish_version(stack, owner.session, purpose="catalog failure good")
    bad = t077.publish_version(stack, owner.session, purpose="catalog failure bad")

    # The authoritative card is corrupt: the rebuild must refuse to derive a
    # projection row from it, record the stable error code and abort.
    st.sql(
        stack.dbname,
        "UPDATE platform.application_versions SET capability_card = '{\"purpose\": 1}'::jsonb "
        f"WHERE id = {st.quote_literal(bad.version_id)}::uuid",
    )
    failed = t077.rebuild_fails(stack, "--all")
    assert failed["error_code"] == "catalog_source_invalid", failed
    state = t077.state_row(stack.dbname)
    assert state["state"] == "failed", state
    assert state["last_error_code"] == "catalog_source_invalid", state
    assert state["last_error_at"] is not None, state
    assert int(state["projection_epoch"]) >= 1, state
    # A failed rebuild never claims the outbox either.
    assert {row["delivered_at"] for row in t077.outbox_rows(stack.dbname)} == {None}

    degraded = t077.search_ok(t077.search(owner.session, ""))
    assert degraded["retrieval"]["source"] == "fallback", degraded["retrieval"]
    assert degraded["retrieval"]["index_state"] == "failed", degraded["retrieval"]
    assert [item["version_id"] for item in degraded["items"]] == [good.version_id], degraded
    assert degraded["retrieval"]["rejected_by_recheck"] >= 1, degraded["retrieval"]
    assert "catalog failure bad" not in t077.search(owner.session, "").text

    # Repairing the authority and rebuilding again clears the error record.
    st.sql(
        stack.dbname,
        "UPDATE platform.application_versions SET capability_card = "
        f"{t077.jsonb(bad.card)} WHERE id = {st.quote_literal(bad.version_id)}::uuid",
    )
    report = t077.rebuild_ok(stack, "--all")
    assert report["state"] == "ready", report
    healthy = t077.state_row(stack.dbname)
    assert healthy["state"] == "ready", healthy
    assert healthy["last_error_code"] is None, healthy
    served = t077.search_ok(t077.search(owner.session, "catalog failure"))
    assert served["retrieval"]["source"] == "index", served["retrieval"]
    assert {item["version_id"] for item in served["items"]} == {
        good.version_id,
        bad.version_id,
    }
