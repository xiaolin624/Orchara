"""T061 (#62) follow-up: an upgraded deployment never strands an ingestion attempt.

Migration 0039 added ``background_jobs.expected_index_version_id`` and backfilled
index versions for documents that were already ``ready``, but it said nothing
about the ``document_ingest`` jobs that were queued or running while the
deployment was upgraded. Those jobs carry a NULL expected index version, so the
T061 completion guard refused them with ``stale_revision`` and left the document
in ``scanning``/``indexing``: a re-ingest of the same artifact returned the same
stuck document (idempotent create) and a same-source rebuild was refused with
``index_build_in_progress``, so the document could never be ingested again.

The tests below run against a real PostgreSQL database and the real compiled
binaries:

1. the genuine upgrade: the schema is built at the pre-0039 state (the same way
   ``test_t061_migration.py`` does), a T055-shaped document + job are seeded, the
   fix migration is applied, the real maintenance handler drains, and the same
   source artifact re-ingests to ``ready`` through real HTTP;
2. a legacy job that survives the migration (defence in depth): the real
   ``document_ingest`` handler plus completion guard must end it and leave the
   document re-ingestable;
3. a document whose build died with no live job: the next ingest heals it into a
   new index version instead of returning the stuck state, while a still-live
   build keeps the T055 idempotent answer;
4. ``index_build_in_progress`` still answers only for a genuinely live build.
"""

from __future__ import annotations

import shutil

from support import f00b, t055, t061
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    build_knowledge_stack,
    fresh_database,
    go_binaries,
    t055_stack,
)

LEGACY_CONTENT = b"legacy source text from before migration 0039\n"
KIND_DOCUMENT_INGEST = "document_ingest"
KIND_ARTIFACT_FINALIZE = "artifact_finalize"
ALL_KINDS = KIND_DOCUMENT_INGEST + "," + KIND_ARTIFACT_FINALIZE

# The stable code the fix writes on an ingestion attempt that can never activate
# because it has no index version bound (a pre-0039 job).
UNBOUND_CODE = "ingest_index_unbound"
# The stable code the recovery path writes on a document whose build died with no
# live job; it marks the abandoned index version failed so a new build can start.
ABANDONED_CODE = "ingest_build_abandoned"


def pre_0039_migrations(tmp_path) -> object:
    """A migrations directory with the chain stopped just before 0039."""
    partial = tmp_path / "migrations-before-t061"
    partial.mkdir()
    migrations_dir = st.PRODUCT_ROOT / "db" / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        # Build a genuinely pre-T061 database. Skipping only T061's own file would
        # leave a gap once a later migration exists, and migration_chain() asserts
        # the applied chain is contiguous.
        if path.name >= "0039":
            continue
        shutil.copy(path, partial / path.name)
    return partial


def setup_owner(stack):
    admin = f00b.admin_session(stack)
    owner = f00b.invite_and_activate(
        stack, admin, "t061.upgrade", password=f00b.USER_PASSWORD
    )
    base_id = t055.create_knowledge_base(owner, "t061 upgrade base").json["id"]
    return owner, base_id


def test_an_upgraded_database_ends_a_legacy_ingest_and_reingests_to_ready(
    fresh_database, go_binaries, api_server, tmp_path
) -> None:
    """The real upgrade path: pre-0039 schema + queued job -> migrate -> re-ingest."""
    dsn, dbname = fresh_database
    partial = pre_0039_migrations(tmp_path)
    completed = st.run_admin(
        go_binaries["admin"], ["migrate", "--migrations-dir", str(partial)], dsn=dsn
    )
    assert completed.returncode == 0, completed.stderr
    assert "index_revisions" not in "".join(st.migration_chain(dbname).values())

    # A real owner (the bootstrap administrator) and real sealed bytes: the
    # re-ingest below is the public HTTP path, not a SQL fiction.
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)
    owner_id = f00b.principal_id_for_login(dbname)
    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    source = f00b.seed_artifact(
        dbname,
        object_dir,
        owner_id,
        content=LEGACY_CONTENT,
        state="ready",
        sealed_content=LEGACY_CONTENT,
        purpose=t055.PURPOSE_KNOWLEDGE,
    )
    base_id = t061.seed_knowledge_base(dbname, owner_id, "t061 legacy base")
    document_id, legacy_job_id = t061.seed_ingest_attempt(
        dbname,
        owner_id=owner_id,
        base_id=base_id,
        source_artifact_id=source.id,
    )

    # Apply the chain to the tip: 0039 backfills only ready documents, so the fix
    # migration is the only thing that can end this queued pre-0039 attempt.
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, completed.stderr
    version = t061.migration_version("ingest_upgrade_recovery", dbname)
    assert st.migration_chain(dbname)[version] == f"{version}_ingest_upgrade_recovery.sql"
    assert int(version) > int(t061.migration_version("index_revisions", dbname))

    job = f00b.job_row(dbname, legacy_job_id)
    assert job["state"] == "failed", job
    assert job["error_code"] == UNBOUND_CODE, job
    assert job["failure_class"] == "business", job
    assert job["finished_at"] is not None, job
    assert job["lease_token"] is None and job["lease_deadline"] is None, job
    row = t055.document_row(dbname, document_id)
    assert row["state"] == "failed", row
    assert row["error_code"] == UNBOUND_CODE, row
    assert row["deleted_at"] is None, row
    assert t061.index_rows(dbname, document_id) == []

    # Re-running the real migrate on the upgraded database is a no-op: it neither
    # re-opens the job nor resurrects the document.
    ledger = st.migration_chain(dbname)
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, completed.stderr
    assert st.migration_chain(dbname) == ledger
    assert f00b.job_row(dbname, legacy_job_id)["state"] == "failed"
    assert t055.document_row(dbname, document_id)["state"] == "failed"

    # The upgraded deployment, for real: compiled API + compiled maintenance.
    stack = build_knowledge_stack(
        tmp_path=tmp_path,
        dsn=dsn,
        dbname=dbname,
        go_binaries=go_binaries,
        api_server=api_server,
    )
    admin = f00b.admin_session(stack)
    completed, reports = f00b.maintenance_once(stack, "--kinds", ALL_KINDS)
    assert f00b.job_reports(reports) == [], reports
    assert t055.document_row(dbname, document_id)["state"] == "failed"

    # Re-ingest with no maintenance process at all: the new attempt is queued
    # (document scanning, job ready, index version building).
    accepted = t055.ingest_document(admin, base_id, source.id)
    assert accepted.status == 202, accepted.text
    assert accepted.json["id"] == document_id, accepted.text
    row = t055.document_row(dbname, document_id)
    assert row["state"] == "scanning", row
    assert int(row["revision"]) == 2, row

    # "Safe to re-run": delete the ledger row and let the real runner apply the
    # fix file again against the live attempt. The replay must not re-open the
    # legacy job nor fail the document under its new live build.
    st.sql(
        dbname,
        "DELETE FROM platform.schema_migrations WHERE version = "
        f"{st.quote_literal(version)}",
    )
    replayed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert replayed.returncode == 0, replayed.stderr
    assert f"{version}_ingest_upgrade_recovery.sql" in (replayed.stdout + replayed.stderr), (
        replayed.stdout + replayed.stderr
    )
    row = t055.document_row(dbname, document_id)
    assert row["state"] == "scanning", row
    assert int(row["revision"]) == 2, row
    assert f00b.job_row(dbname, legacy_job_id)["state"] == "failed"
    # The legacy attempt never created an index version, so the retried document
    # starts again at build number 1, now bound to source revision 2.
    live_indexes = t061.index_rows(dbname, document_id)
    assert len(live_indexes) == 1, live_indexes
    live_index = live_indexes[0]
    assert int(live_index["version"]) == 1, live_index
    assert int(live_index["document_revision"]) == 2, live_index
    assert live_index["state"] == "building", live_index
    live_job = t061.job_for_index(dbname, document_id, live_index["id"])
    assert live_job["state"] == "ready", live_job

    with t055.ingest_pools(stack, tmp_path / "maintenance-t061-upgrade.log"):
        view = t055.wait_for_document_state(admin, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(dbname, document_id))

    row = t055.document_row(dbname, document_id)
    assert row["state"] == "ready", row
    assert int(row["revision"]) == 2, row
    assert int(row["active_revision"]) == 2, row
    jobs = t061.document_jobs(dbname, document_id)
    assert [item["state"] for item in jobs] == ["failed", "succeeded"], jobs
    assert jobs[0]["error_code"] == UNBOUND_CODE, jobs
    active = t061.active_index(dbname, document_id)
    assert active is not None, t055.document_row(dbname, document_id)
    assert active["state"] == "ready"
    assert int(active["document_revision"]) == 2
    assert t061.chunk_rows_for_index(dbname, active["id"])
    retrieved = t055.retrieval_query(admin, base_id, "legacy source text", top_k=5)
    assert retrieved.status == 200, retrieved.text
    assert retrieved.json["items"], retrieved.text


def test_the_guard_ends_a_legacy_job_that_survived_the_migration(t055_stack) -> None:
    """Defence in depth: a NULL-version job must not strand the document either."""
    stack = t055_stack
    owner, base_id = setup_owner(stack)
    artifact_id = t055.prepare_source(stack, owner, LEGACY_CONTENT)
    document_id, job_id = t061.seed_ingest_attempt(
        stack.dbname,
        owner_id=owner.principal_id,
        base_id=base_id,
        source_artifact_id=artifact_id,
    )

    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-t061-legacy-job.log"
    ):
        view = t055.wait_for_document_state(owner, document_id, {"failed"})
    assert view["state"] == "failed", (view, t055.document_row(stack.dbname, document_id))

    job = f00b.job_row(stack.dbname, job_id)
    assert job["state"] == "failed", job
    assert job["error_code"] == UNBOUND_CODE, job
    assert job["failure_class"] == "business", job
    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "failed", row
    assert row["error_code"] == UNBOUND_CODE, row
    assert row["active_revision"] is None, row
    assert t061.index_rows(stack.dbname, document_id) == []

    # The document is retryable: the same artifact re-ingests and reaches ready.
    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-t061-legacy-reingest.log"
    ):
        accepted = t055.ingest_document(owner, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        assert accepted.json["id"] == document_id, accepted.text
        retried = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert retried["state"] == "ready", (retried, t055.document_row(stack.dbname, document_id))
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and active["state"] == "ready"
    assert int(active["document_revision"]) == 2


def test_a_reingest_while_the_build_is_live_is_still_the_same_intent(t055_stack) -> None:
    """The recovery path must not turn the T055 idempotent answer into a new build."""
    stack = t055_stack
    owner, base_id = setup_owner(stack)
    artifact_id = t055.prepare_source(stack, owner, t055.SAMPLE_DOCUMENT)

    first = t055.ingest_document(owner, base_id, artifact_id)
    assert first.status == 202, first.text
    document_id = first.json["id"]
    again = t055.ingest_document(owner, base_id, artifact_id)
    assert again.status == 202, again.text
    assert again.json["id"] == document_id, again.text

    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "scanning", row
    assert int(row["revision"]) == 1, row
    jobs = t061.document_jobs(stack.dbname, document_id)
    assert len(jobs) == 1, jobs
    assert jobs[0]["state"] == "ready", jobs
    indexes = t061.index_rows(stack.dbname, document_id)
    assert [item["state"] for item in indexes] == ["building"], indexes

    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-t061-live-idempotent.log"
    ):
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    indexes = t061.index_rows(stack.dbname, document_id)
    assert len(indexes) == 1, indexes
    assert int(t061.active_index(stack.dbname, document_id)["document_revision"]) == 1


def test_a_reingest_heals_a_build_that_died_without_a_live_job(t055_stack) -> None:
    """A document stuck scanning with no live job must be ingestable again."""
    stack = t055_stack
    owner, base_id = setup_owner(stack)
    artifact_id = t055.prepare_source(stack, owner, t055.SAMPLE_DOCUMENT)

    accepted = t055.ingest_document(owner, base_id, artifact_id)
    assert accepted.status == 202, accepted.text
    document_id = accepted.json["id"]
    jobs = t061.document_jobs(stack.dbname, document_id)
    assert len(jobs) == 1 and jobs[0]["state"] == "ready", jobs
    assert t055.document_row(stack.dbname, document_id)["state"] == "scanning"

    # The deployment lost the attempt: the lease sweep fails the job and writes
    # nothing to the document, so no job can ever finish this build.
    t061.abandon_job_like_the_lease_sweep(stack.dbname, jobs[0]["id"])
    assert t055.document_row(stack.dbname, document_id)["state"] == "scanning"

    healed = t055.ingest_document(owner, base_id, artifact_id)
    assert healed.status == 202, healed.text
    assert healed.json["id"] == document_id, healed.text
    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "scanning", row
    assert int(row["revision"]) == 2, row
    jobs = t061.document_jobs(stack.dbname, document_id)
    assert [item["state"] for item in jobs] == ["failed", "ready"], jobs
    abandoned = t061.index_by_version(stack.dbname, document_id, 1)
    assert abandoned["state"] == "failed", abandoned
    assert abandoned["error_code"] == ABANDONED_CODE, abandoned
    assert abandoned["activated_at"] is None, abandoned
    assert t061.index_by_version(stack.dbname, document_id, 2)["state"] == "building"

    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-t061-healed.log"
    ):
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and int(active["version"]) == 2
    assert int(active["document_revision"]) == 2
    assert t061.index_by_version(stack.dbname, document_id, 1)["state"] == "failed"


def test_index_build_in_progress_needs_a_genuinely_live_build(t055_stack) -> None:
    """The 409 is about a live build, not about the document's scanning state."""
    stack = t055_stack
    owner, _base_id, _artifact_id, document_id = t061.setup_ready_document(
        stack, log_name="live-build"
    )
    assert int(t061.active_index(stack.dbname, document_id)["version"]) == 1

    accepted = t061.reindex(owner, document_id)
    assert accepted.status == 202, accepted.text
    assert int(accepted.json["index_version"]) == 2
    live_index_id = accepted.json["index_version_id"]

    # Genuinely live: the queued job plus its still-building index version.
    refused = t061.reindex(owner, document_id)
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] == "index_build_in_progress", refused.text
    assert t055.document_row(stack.dbname, document_id)["state"] == "scanning"

    # The attempt dies (the lease sweep has no document writer), so index v2 can
    # never finish. The refused rebuild must become possible again.
    job = t061.job_for_index(stack.dbname, document_id, live_index_id)
    t061.abandon_job_like_the_lease_sweep(stack.dbname, job["id"])
    restarted = t061.reindex(owner, document_id)
    assert restarted.status == 202, restarted.text
    assert int(restarted.json["index_version"]) == 3
    abandoned = t061.index_by_version(stack.dbname, document_id, 2)
    assert abandoned["state"] == "failed", abandoned
    assert abandoned["error_code"] == ABANDONED_CODE, abandoned
    assert abandoned["activated_at"] is None, abandoned
    # The previous active index stayed readable throughout the recovery.
    assert int(t061.active_index(stack.dbname, document_id)["version"]) == 1

    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-t061-restarted.log"
    ):
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and int(active["version"]) == 3
    assert t061.index_by_version(stack.dbname, document_id, 1)["state"] == "superseded"
    assert t061.index_by_version(stack.dbname, document_id, 2)["state"] == "failed"
