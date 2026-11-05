"""F11/T076 (#77): one regression suite replays every deletion governance gate.

The security evaluator's question is not "is there a test for each ticket" but "does
the whole chain still hold when replayed with the real entry points, and where is
the evidence for each gate". This suite drives one document end to end and records
the evidence at every gate, then repeats the same gates for a Run's diagnostic
grant and for a late writer:

``deletion_accepted`` -> ``ledger_tombstone`` -> ``read_refusal`` -> ``purge`` ->
``copy_ledger`` -> ``grant_read`` -> ``grant_revoked`` -> ``grant_deleted_content``
-> ``late_writer`` -> ``final_read_refusal``.

Every fact in the evidence record is read back from PostgreSQL, the real object
store or the real HTTP response; nothing is inferred from a flag. The test fails if
any gate is missing, so an entry can never silently drop out of the suite.
"""

from __future__ import annotations

import json

from support import f00b, t028, t055, t069, t071, t072, t073, t076
from support import stack as st

# The closed gate vocabulary this suite proves. The report lists each gate with this
# test's node id as its evidence.
GATES = (
    "deletion_accepted",
    "ledger_tombstone",
    "read_refusal",
    "purge",
    "copy_ledger",
    "grant_read",
    "grant_revoked",
    "grant_deleted_content",
    "late_writer",
    "final_read_refusal",
)


def test_the_whole_deletion_governance_chain_has_evidence_at_every_gate(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    evidence: list[dict] = []

    def record(gate: str, **facts: object) -> None:
        assert gate in GATES, gate
        evidence.append({"gate": gate, **facts})

    alice = t069.owner(stack)
    content = (
        "T076 治理链正文：GOVERNANCE-MARKER-7766\n"
        "退款政策：订单完成后三十天内可申请，marker-t076-governance。\n"
    )
    base_id, document_id, source_artifact = t071.ready_document(
        stack, alice.creator, content, log_path=tmp_path / "t076-governance-ingest.log"
    )
    object_keys = t071.document_object_keys(stack, document_id)
    query = "退款政策"
    warmed = t055.retrieval_query(alice.creator, base_id, query, top_k=5)
    assert warmed.status == 200 and warmed.json["items"], warmed.json
    deleted_chunk = warmed.json["items"][0]["chunk_id"]
    assert t071.cache_rows(stack.dbname, base_id=base_id)

    # gate 1: the logical delete is accepted and the job exists before any cleanup.
    job = t069.deletion_of(
        t069.delete_resource(alice.creator, t069.RESOURCE_DOCUMENT, document_id)
    )
    stored = t071.deletion_job(stack.dbname, job["id"])
    assert stored["state"] in {"pending", "running"}, stored
    assert set(t071.remaining(stored)) == set(t069.SCOPES), stored
    assert t076.background_jobs(
        stack.dbname, kind=t076.KIND_DELETE_CONTENT, target_id=job["id"]
    ), "the purge unit of work must exist in the delete transaction"
    audit = t069.audit_events(stack.dbname, "deletion.requested", resource_id=document_id)
    assert len(audit) == 1, audit
    record(
        "deletion_accepted",
        deletion_id=job["id"],
        state=stored["state"],
        remaining_scopes=sorted(t071.remaining(stored)),
        audit_rows=len(audit),
    )

    # gate 2: the independent, hash-chained ledger recorded the decision.
    entries = t072.entries_for_job(stack.dbname, job["id"])
    assert [entry["entry_kind"] for entry in entries] == ["tombstone"], entries
    record(
        "ledger_tombstone",
        sequence=entries[0]["sequence"],
        record_sha256=entries[0]["record_sha256"],
        prev_sha256=entries[0]["prev_sha256"],
    )

    # gate 3: every read refuses immediately, before any physical cleanup.
    st.assert_error(
        t055.get_document(alice.creator, document_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    assert t055.list_documents(alice.creator, base_id).json["items"] == []
    st.assert_error(
        t055.get_citation(alice.creator, deleted_chunk), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    after_delete = t055.retrieval_query(alice.creator, base_id, query, top_k=5)
    assert after_delete.json["items"] == [], after_delete.json
    assert after_delete.json["retrieval"]["cache"]["hit"] is False, after_delete.json
    assert "GOVERNANCE-MARKER-7766" not in after_delete.text
    record(
        "read_refusal",
        document_get=404,
        citation_open=404,
        retrieval_items=0,
        cache_hit=False,
    )

    # gate 4: the T071 purge clears the objects, index rows and cache entries.
    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports
    purged = t071.deletion_job(stack.dbname, job["id"])
    assert set(t071.completed(purged)) == set(t069.T071_SCOPES), purged
    for key in object_keys:
        assert not t071.object_exists(stack, key), key
    assert t071.chunk_rows(stack.dbname, document_id) == []
    assert t071.cache_rows(stack.dbname, base_id=base_id) == []
    record(
        "purge",
        completed_scopes=sorted(t071.completed(purged)),
        objects_gone=len(object_keys),
        chunks=0,
        cache_rows=0,
    )

    # gate 5: the T072 copy pass records every copy class and completes the job;
    # the third-party/backup boundary is recorded, not claimed erased.
    sandbox_root = tmp_path / "t076-governance-sandbox"
    sandbox_root.mkdir()
    _completed, copy_reports = t072.copies_ok(stack, sandbox_root=sandbox_root)
    completed_job = t071.deletion_job(stack.dbname, job["id"])
    assert completed_job["state"] == "completed", completed_job
    assert t071.remaining(completed_job) == [], completed_job
    kinds = [entry["entry_kind"] for entry in t072.entries_for_job(stack.dbname, job["id"])]
    assert "job_completed" in kinds, kinds
    outcomes = [
        report for report in copy_reports if "scope" in report and "outcome" in report
    ]
    assert outcomes, copy_reports
    third_party = [
        outcome for outcome in outcomes if outcome["scope"] == "third_party_history"
    ]
    assert third_party, outcomes
    record(
        "copy_ledger",
        completed_scopes=sorted(t071.completed(completed_job)),
        ledger_entry_kinds=sorted(set(kinds)),
        third_party_outcome=third_party[0]["outcome"],
        copy_classes=sorted({outcome["copy_class"] for outcome in outcomes}),
    )

    # gate 6/7/8: the T073 diagnostic door. A grant serves the real output bytes of a
    # live Run, revocation refuses immediately, and deleting that content through the
    # real endpoint refuses the same grant (the tombstone wins over a live grant).
    # The run belongs to the same creator: one database has exactly one golden draft.
    admin = t073.admin_session(stack)
    _owner, run, lease = t073.claimed_run(stack, alice)
    output_content = b"T076 governance grant marker: the real diagnostic body\n"
    output_artifact = t073.upload_output(stack, lease, output_content)
    grant = t073.create_grant_ok(
        alice.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(output_artifact)],
    )
    granted = t073.read_content(admin, grant["id"], t073.artifact_scope(output_artifact))
    assert granted.status == 200, granted.text
    assert granted.raw == output_content
    audit_rows = t073.content_read_rows(stack.dbname)
    assert len(audit_rows) == 1, audit_rows
    record(
        "grant_read",
        grant_id=grant["id"],
        run_id=run["id"],
        audit_rows=len(audit_rows),
        bytes=len(granted.raw),
    )

    revoked = t073.revoke_grant(alice.creator, grant["id"])
    assert revoked.status == 204, revoked.text
    refused_after_revoke = t073.read_content(
        admin, grant["id"], t073.artifact_scope(output_artifact)
    )
    st.assert_error(refused_after_revoke, 403, t073.CODE_GRANT_REVOKED)
    record("grant_revoked", grant_id=grant["id"], code=t073.CODE_GRANT_REVOKED)

    second_grant = t073.create_grant_ok(
        alice.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(output_artifact)],
    )
    deleted_output = t069.deletion_of(
        t069.delete_resource(alice.creator, t069.RESOURCE_ARTIFACT, output_artifact)
    )
    refused_deleted = t073.read_content(
        admin, second_grant["id"], t073.artifact_scope(output_artifact)
    )
    # The object's own tombstone is the refusal (the Run is still alive, in
    # cancelling): a grant must never outlive the row it names.
    st.assert_error(refused_deleted, 404, t073.CODE_CONTENT_UNAVAILABLE)
    assert output_content not in refused_deleted.raw
    record(
        "grant_deleted_content",
        grant_id=second_grant["id"],
        deletion_id=deleted_output["id"],
        code=t073.CODE_CONTENT_UNAVAILABLE,
    )

    # gate 9: a late writer cannot resurrect the deleted content. A replayed finalize
    # job for the deleted run output fails with the named code and writes no bytes.
    deleted_row = t069.artifact_row(stack.dbname, output_artifact)
    sealed_before = t076.sealed_keys(stack)
    late = t076.enqueue_late_finalize(
        stack,
        artifact={**deleted_row, "expected_sha256": deleted_row["sha256"]},
        owner_id=alice.creator.principal_id,
        suffix="governance",
    )
    _completed, _late_reports = f00b.maintenance_once(
        stack, "--kinds", t076.KIND_ARTIFACT_FINALIZE
    )
    late_job = t076.job_failure(stack.dbname, late["id"])
    assert late_job["state"] == "failed", late_job
    assert late_job["error_code"] == t076.CODE_SOURCE_DELETED, late_job
    assert t076.sealed_keys(stack) == sealed_before
    record(
        "late_writer",
        kind=t076.KIND_ARTIFACT_FINALIZE,
        background_job_id=late["id"],
        error_code=late_job["error_code"],
        failure_class=late_job["failure_class"],
        new_sealed_objects=0,
    )

    # gate 10: after the whole chain, the deleted document and the deleted output are
    # still unreadable and the late writer changed nothing.
    st.assert_error(
        t055.get_document(alice.creator, document_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    st.assert_error(
        t028.get_artifact(alice.creator, source_artifact), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    assert t071.chunk_rows(stack.dbname, document_id) == []
    empty = t055.retrieval_query(alice.creator, base_id, query, top_k=5)
    assert empty.json["items"] == [], empty.json
    assert "GOVERNANCE-MARKER-7766" not in empty.text
    record(
        "final_read_refusal",
        document_get=404,
        source_artifact_get=404,
        retrieval_items=0,
        chunks=0,
    )

    # The suite is the single evidence place: every documented gate was replayed.
    recorded = [item["gate"] for item in evidence]
    assert recorded == list(GATES), recorded
    # And no evidence record carries a body, an object key or a content hash.
    serialized = json.dumps(evidence)
    assert "GOVERNANCE-MARKER-7766" not in serialized
    for key in object_keys:
        assert key not in serialized, key
    for value in evidence:
        assert "object_key" not in value, value
