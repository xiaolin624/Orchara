"""T101/J4: the knowledge ingestion journey.

The source document is uploaded through the real signed-link upload, sealed by the
real ``artifact_finalize`` maintenance handler, ingested by the real
``document_ingest`` maintenance handler, then retrieved through the knowledge
query route. The citation is checked against an **independently recomputed**
truth: the sealed source bytes' SHA-256 and the rune slice named by the citation's
span, never just the chunk row the product returns.

Deleting the document must stop the citation immediately (404, no body, no stale
cache fallback) and drop it from retrieval.
"""

from __future__ import annotations

import hashlib
import json

from support import f00b, t055
from support import stack as st


def run(stack, record) -> dict:
    record.command("python -m pytest tests/f12/test_t101_journeys.py -k knowledge")
    admin = f00b.admin_session(stack)
    owner = f00b.invite_and_activate(stack, admin, "t101.knowledge", roles=("user",))
    stranger = f00b.invite_and_activate(stack, admin, "t101.knowledge-stranger", roles=("user",))
    record.command(
        "POST /api/v1/admin/users -> POST /api/v1/auth/activate (owner + stranger)"
    )
    record.truth_source("sealed source object bytes: sha256 + rune slice recomputed in the test")
    record.truth_source(
        "platform.knowledge_documents / platform.knowledge_chunks / platform.background_jobs via psql"
    )

    # -- 1. knowledge base + source upload -> finalize -> ready ---------------
    base = t055.create_knowledge_base(owner, "t101 knowledge base")
    record.http("POST", "/api/v1/knowledge-bases", base)
    record.check("knowledge base created", base.status == 201, base.text)
    base_id = base.json["id"]

    source_id = t055.prepare_source(stack, owner, t055.SAMPLE_DOCUMENT, media_type=t055.TEXT)
    record.http("POST", "/api/v1/artifacts/uploads + PUT signed link + complete + finalize", None)
    source_artifact = t055.artifact_row(stack.dbname, source_id)
    record.check(
        "the source artifact is truly sealed and ready",
        source_artifact["state"] == "ready"
        and source_artifact["purpose"] == t055.PURPOSE_KNOWLEDGE,
        source_artifact,
    )

    # -- 2. document ingest -> ready ---------------------------------------
    log_path = stack.server.log_path.parent / "t101-knowledge-maintenance.log"
    with t055.ingest_pools(stack, log_path):
        accepted = t055.ingest_document(owner, base_id, source_id)
        record.http("POST", "/api/v1/knowledge-bases/{id}/documents", accepted)
        record.check(
            "the document ingest is accepted in the scanning state",
            accepted.status == 202 and accepted.json["state"] == "scanning",
            accepted.text,
        )
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    record.command("cmd/maintenance run --once --kinds document_ingest")
    record.check("the document reaches ready", view["state"] == "ready", view)
    document = t055.document_row(stack.dbname, document_id)
    chunks = t055.chunk_rows(stack.dbname, document_id, 1)
    jobs = t055.document_jobs(stack.dbname, document_id)
    record.check(
        "the durable document row is ready at revision 1 with a succeeded job",
        document["state"] == "ready"
        and int(document["revision"]) == 1
        and document["error_code"] is None
        and jobs
        and jobs[0]["state"] == "succeeded",
        {"document": document, "jobs": jobs},
    )
    record.check(
        "the ingestion produced ordered traceable chunks",
        len(chunks) >= 3
        and [int(chunk["chunk_no"]) for chunk in chunks] == list(range(len(chunks))),
        len(chunks),
    )

    # -- 3. retrieval -> citation that resolves to the real source bytes -----
    retrieved = t055.retrieval_query(owner, base_id, "退款政策", top_k=3)
    record.http("POST", "/api/v1/knowledge-bases/{id}/retrieval-queries", retrieved)
    record.check(
        "the retrieval returns ranked items",
        retrieved.status == 200 and retrieved.json["items"],
        retrieved.text[:500],
    )
    top = retrieved.json["items"][0]
    top_id = top["chunk_id"]

    opened = t055.get_citation(owner, top_id)
    record.http("GET", f"/api/v1/knowledge-citations/{top_id}", opened)
    record.check(
        "the citation opens and is the same body the query returned",
        opened.status == 200 and opened.json["text"] == top["text"],
        opened.text[:400],
    )

    # Independent truth: recompute the citation from the sealed source bytes.
    source_bytes = (stack.object_dir / source_artifact["object_key"]).read_bytes()
    source_text = source_bytes.decode("utf-8")
    expected_sha = hashlib.sha256(source_bytes).hexdigest()
    citation = opened.json["citation"]
    span = top["span"]
    start, end = int(span["start_char"]), int(span["end_char"])
    recomputed = "".join(list(source_text)[start:end])
    record.check(
        "the citation's source hash is the sealed source object's real sha256",
        citation["source_sha256"] == expected_sha,
        {"citation": citation["source_sha256"], "recomputed": expected_sha},
    )
    record.check(
        "the citation span resolves to exactly the returned text in the sealed source bytes",
        recomputed == top["text"],
        {"recomputed_prefix": recomputed[:60], "text_prefix": top["text"][:60]},
    )
    record.check(
        "the citation names the document, revision and chunk number",
        citation["document_id"] == document_id
        and int(citation["document_revision"]) == 1
        and int(citation["chunk_no"]) == int(top["chunk_no"])
        and citation["source_artifact_id"] == source_id,
        citation,
    )
    chunk_row = next(chunk for chunk in chunks if chunk["id"] == top_id)
    chunk_text = t055.object_text(
        stack.object_dir, t055.artifact_row(stack.dbname, chunk_row["body_artifact_id"])
    )
    record.check(
        "the chunk body artifact bytes are the same text",
        chunk_text == top["text"],
        chunk_text[:80],
    )

    # -- 4. ACL: another human cannot query or open the citation ------------
    foreign_query = t055.retrieval_query(stranger, base_id, "退款政策", top_k=3)
    record.http("POST", "/api/v1/knowledge-bases/{id}/retrieval-queries (stranger)", foreign_query)
    record.check(
        "another human gets the same not-found shape for the base",
        foreign_query.status == 404, foreign_query.text,
    )
    foreign_citation = t055.get_citation(stranger, top_id)
    record.http("GET", f"/api/v1/knowledge-citations/{top_id} (stranger)", foreign_citation)
    record.check(
        "another human cannot open the citation",
        foreign_citation.status == 404, foreign_citation.text,
    )

    # -- 5. delete: citation 404 immediately, retrieval loses it ------------
    deleted = owner.client.request(
        "DELETE", f"/api/v1/knowledge-documents/{document_id}", csrf=owner.csrf
    )
    record.http("DELETE", f"/api/v1/knowledge-documents/{document_id}", deleted)
    record.check(
        "deletion is accepted with a readable job",
        deleted.status == 202
        and deleted.json["resource_id"] == document_id
        and deleted.json["state"] in {"pending", "running", "completed"},
        deleted.text,
    )
    refused = t055.get_citation(owner, top_id)
    record.http("GET", f"/api/v1/knowledge-citations/{top_id} (after delete)", refused)
    st.assert_error(refused, 404, "resource_not_found")
    record.check(
        "the deleted citation is a 404 with no body/citation fallback",
        refused.status == 404
        and "text" not in refused.json
        and "citation" not in refused.json,
        refused.text,
    )
    after = t055.retrieval_query(owner, base_id, "退款政策", top_k=3)
    record.http("POST", "/api/v1/knowledge-bases/{id}/retrieval-queries (after delete)", after)
    record.check(
        "retrieval no longer returns the deleted document (no cache fallback)",
        after.status == 200 and after.json["items"] == [],
        after.text[:500],
    )
    record.note(
        "deletion",
        {
            "citation_status": refused.status,
            "retrieval_items": len(after.json["items"]),
            "cache": after.json.get("retrieval", {}).get("cache"),
        },
    )

    # -- 6. the deletion job is a real background job ----------------------
    record.truth_source("GET /api/v1/deletions/{job_id} (job state/remaining scopes)")
    job_read = owner.client.get(f"/api/v1/deletions/{deleted.json['id']}")
    record.http("GET", f"/api/v1/deletions/{deleted.json['id']}", job_read)
    record.check(
        "the deletion job is readable with its remaining scopes",
        job_read.status == 200
        and job_read.json["state"] in ("pending", "running", "completed", "failed")
        and job_read.json["remaining_scopes"],
        job_read.text[:400],
    )
    document_after = t055.get_document(owner, document_id)
    record.check(
        "the deleted document is a 404 through the document route",
        document_after.status == 404, document_after.text,
    )

    return {
        "knowledge_base_id": base_id,
        "document_id": document_id,
        "source_artifact_id": source_id,
        "chunks": len(chunks),
        "chunk_id": top_id,
        "citation_sha256": citation["source_sha256"],
        "deleted_citation_status": refused.status,
    }
