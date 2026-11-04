"""T055 acceptance: txt/md upload -> ready through the real background pipeline.

The test drives the public path end to end: the existing artifact upload and
finalize flow, one ingestDocument call, a real maintenance process with the real
document_ingest handler, and the SQL-filtered retrieval answer. The chunk
citation is then checked against an independent truth: the sealed source bytes,
their SHA-256, a rune slice recomputed from the recorded span, and the sealed
chunk body. The API is never trusted as its own oracle.
"""

from __future__ import annotations

import json

from support import f00b, t055
from support.fixtures import t055_stack  # noqa: F401 - pytest fixture

EMBEDDING_REVISION = "local-hash-v1"
DIMENSION = 384


def setup_user(stack):
    admin = f00b.admin_session(stack)
    alice = f00b.invite_and_activate(stack, admin, "t055.alice", password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, "t055 base").json["id"]
    return alice, base_id


def test_txt_upload_becomes_ready_with_traceable_chunks(t055_stack) -> None:
    stack = t055_stack
    alice, base_id = setup_user(stack)
    artifact_id = t055.prepare_source(stack, alice, t055.SAMPLE_DOCUMENT, media_type=t055.TEXT)

    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t055-txt.log"):
        accepted = t055.ingest_document(alice, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        assert set(accepted.json) == {"id", "state"}, accepted.json
        assert accepted.json["state"] == "scanning"
        document_id = accepted.json["id"]

        view = t055.wait_for_document_state(alice, document_id, {"ready", "failed"})
        assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))

    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "ready"
    assert int(row["revision"]) == 1
    assert row["active_revision"] is not None and int(row["active_revision"]) == 1
    assert row["parser_version"] == "orchara-text-parser/1"
    assert row["chunker_version"] == "orchara-paragraph-overlap-chunker/1"
    assert row["embedding_model_revision"] == EMBEDDING_REVISION
    assert row["error_code"] is None

    chunks = t055.chunk_rows(stack.dbname, document_id, 1)
    assert len(chunks) >= 3, chunks
    assert [int(chunk["chunk_no"]) for chunk in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert int(chunk["embedding_length"]) == DIMENSION
        assert int(chunk["embedding_dimension"]) == DIMENSION
        assert chunk["embedding_model_revision"] == EMBEDDING_REVISION
        body = t055.artifact_row(stack.dbname, chunk["body_artifact_id"])
        assert body["state"] == "ready"
        assert body["purpose"] == "knowledge_chunk"
        assert body["owner_id"] == alice.principal_id
        citation = t055.citation_of(chunk)
        assert citation["document_id"] == document_id
        assert int(citation["document_revision"]) == 1
        assert int(citation["chunk_no"]) == int(chunk["chunk_no"])
        assert citation["source_artifact_id"] == artifact_id
        assert len(citation["source_sha256"]) == 64
        assert citation["embedding_model_revision"] == EMBEDDING_REVISION
        assert citation["offset_unit"] == "rune"

    jobs = t055.document_jobs(stack.dbname, document_id)
    assert len(jobs) == 1, jobs
    assert jobs[0]["state"] == "succeeded", jobs
    assert int(jobs[0]["attempt_count"]) == 1

    # Retrieval: ranked, non-empty, and the answer metadata records the versions.
    retrieved = t055.retrieval_query(alice, base_id, "华北地区十月的销售额是多少", top_k=5)
    assert retrieved.status == 200, retrieved.text
    payload = retrieved.json
    assert set(payload) == {"items", "next_cursor", "retrieval"}, payload
    assert payload["next_cursor"] is None
    assert payload["retrieval"] == {
        "embedding_model_revision": EMBEDDING_REVISION,
        "top_k": 5,
        "acl_epoch": 1,
    }, payload["retrieval"]
    items = payload["items"]
    assert items, payload
    assert len(items) <= 5
    assert "华北地区" in items[0]["text"], items[0]
    top = items[0]
    assert set(top) >= {
        "chunk_id", "document_id", "document_revision", "document_state",
        "chunk_no", "score", "text", "span", "citation",
    }
    assert top["document_id"] == document_id
    assert int(top["document_revision"]) == 1
    assert top["document_state"] == "ready"
    assert -1.0 <= float(top["score"]) <= 1.0
    scored = [float(item["score"]) for item in items]
    assert scored == sorted(scored, reverse=True), scored

    # Independent truth check for the citation: sealed source bytes -> hash and
    # rune slice recomputed here, never read back from the chunk row.
    source = t055.artifact_row(stack.dbname, artifact_id)
    source_bytes = (stack.object_dir / source["object_key"]).read_bytes()
    assert t055.sha256_hex(source_bytes) == top["citation"]["source_sha256"]
    text = source_bytes.decode("utf-8")
    runes = list(text)
    span = top["span"]
    start, end = int(span["start_char"]), int(span["end_char"])
    assert t055.sha256_hex(source_bytes) == top["citation"]["source_sha256"]
    assert "".join(runes[start:end]) == top["text"]
    assert int(span["start_line"]) == text[:start].count("\n") + 1
    assert int(span["end_line"]) == text[: end - 1].count("\n") + 1

    # The chunk body artifact holds exactly the chunk text, sealed and ready.
    chunk_row = next(chunk for chunk in chunks if chunk["id"] == top["chunk_id"])
    assert t055.object_text(stack.object_dir, t055.artifact_row(
        stack.dbname, chunk_row["body_artifact_id"]
    )) == top["text"]

    # Opening the citation re-checks ACL/current revision and returns the same body.
    opened = t055.get_citation(alice, top["chunk_id"])
    assert opened.status == 200, opened.text
    assert opened.json["chunk_id"] == top["chunk_id"]
    assert opened.json["text"] == top["text"]
    assert opened.json["citation"] == top["citation"]


def test_markdown_uses_its_own_parser_revision_and_queries_within_the_same_kb(t055_stack) -> None:
    stack = t055_stack
    alice, base_id = setup_user(stack)
    artifact_id = t055.prepare_source(
        stack,
        alice,
        "# 知识库\n\nMarkdown 里也有可检索的段落：季度目标是 88 万元。\n",
        media_type=t055.MARKDOWN,
    )

    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t055-md.log"):
        accepted = t055.ingest_document(alice, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(alice, document_id, {"ready", "failed"})

    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    row = t055.document_row(stack.dbname, document_id)
    assert row["parser_version"] == "orchara-markdown-parser/1"
    retrieved = t055.retrieval_query(alice, base_id, "季度目标是多少")
    assert retrieved.status == 200, retrieved.text
    assert retrieved.json["items"]
    assert any("季度目标" in item["text"] for item in retrieved.json["items"])


def test_retrieval_filters_acl_and_current_revision_before_ranking(t055_stack) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice = f00b.invite_and_activate(stack, admin, "t055.alice", password=f00b.USER_PASSWORD)
    bob = f00b.invite_and_activate(stack, admin, "t055.bob", password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, "t055 acl").json["id"]
    artifact_id = t055.prepare_source(stack, alice, t055.SAMPLE_DOCUMENT)

    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t055-acl.log"):
        accepted = t055.ingest_document(alice, base_id, artifact_id)
        document_id = accepted.json["id"]
        t055.wait_for_document_state(alice, document_id, {"ready", "failed"})

    assert t055.document_row(stack.dbname, document_id)["state"] == "ready"
    chunks = t055.chunk_rows(stack.dbname, document_id, 1)
    assert chunks

    # A row for a non-current revision is invisible to retrieval even though it
    # exists, references a ready body artifact and would score well.
    forged_id = f00b.new_uuid()
    citation = t055.citation_of(chunks[0])
    citation["document_revision"] = 2
    citation["chunk_no"] = 0
    t055.st.sql(
        stack.dbname,
        "INSERT INTO platform.knowledge_chunks "
        "(id, document_id, document_revision, chunk_no, body_artifact_id, citation, "
        " embedding_model_revision, embedding, embedding_dimension) "
        f"SELECT {t055.st.quote_literal(forged_id)}::uuid, "
        f"{t055.st.quote_literal(document_id)}::uuid, 2, 0, "
        f"{t055.st.quote_literal(chunks[0]['body_artifact_id'])}::uuid, "
        f"{t055.st.quote_literal(json.dumps(citation))}::jsonb, embedding_model_revision, "
        "embedding, embedding_dimension "
        "FROM platform.knowledge_chunks "
        f"WHERE id = {t055.st.quote_literal(chunks[0]['id'])}::uuid",
    )
    assert t055.chunk_count(stack.dbname, document_id) == len(chunks) + 1
    current = t055.retrieval_query(alice, base_id, "退款政策", top_k=20)
    assert current.status == 200, current.text
    assert current.json["items"]
    assert all(int(item["document_revision"]) == 1 for item in current.json["items"])
    assert all(item["chunk_id"] != forged_id for item in current.json["items"])

    # A foreign principal and an administrator cannot even name the KB or chunk.
    for caller in (bob, admin):
        assert t055.retrieval_query(caller, base_id, "退款政策").status == 404
        assert t055.get_citation(caller, chunks[0]["id"]).status == 404

    # Revoking the document (tombstone written the way F07 will) makes both the
    # retrieval result empty and the citation 404, without a cached body.
    t055.st.sql(
        stack.dbname,
        "UPDATE platform.knowledge_documents SET state = 'deleted', deleted_at = now() "
        f"WHERE id = {t055.st.quote_literal(document_id)}::uuid",
    )
    empty = t055.retrieval_query(alice, base_id, "退款政策")
    assert empty.status == 200, empty.text
    assert empty.json["items"] == []
    revoked = t055.get_citation(alice, chunks[0]["id"])
    assert revoked.status == 404, revoked.text
    assert revoked.json["error"]["code"] == "resource_not_found"
    assert t055.get_document(alice, document_id).status == 404
