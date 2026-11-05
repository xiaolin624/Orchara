"""F10/T060 acceptance: plain-text PDFs keep page-numbered citations.

Every test drives the public path: the real artifact upload/finalize flow (really
admitted bytes), one ingestDocument call, a real maintenance process with the real
document_ingest handler, and the SQL-filtered retrieval/citation reads. Chunk page
attribution is checked against the committed hand-checked reference, not against
the platform's own answer. An image-only page contributes no text; a document
without a text layer is a diagnosable failure and never a ready document.
"""

from __future__ import annotations

from support import f00b, t028, t055, t060
from support.fixtures import t060_stack  # noqa: F401 - pytest fixture


def setup_user(stack, login: str = "t060.alice"):
    admin = f00b.admin_session(stack)
    alice = f00b.invite_and_activate(stack, admin, login, password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, f"{login} knowledge").json["id"]
    return alice, base_id


def ingest_ready(stack, alice, base_id, content, log_name):
    artifact_id = t060.upload_source(stack, alice, content)
    document_id, view = t060.ingest_and_wait(stack, alice, base_id, artifact_id, log_name)
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    return artifact_id, document_id


def assert_pdf_citation_shape(citation: dict) -> None:
    assert citation["pdf"]["page_start"] >= 1
    assert citation["pdf"]["page_end"] >= citation["pdf"]["page_start"]
    assert citation["pdf"]["page_char_end"] > citation["pdf"]["page_char_start"]
    assert citation["pdf"]["page_count"] >= 1
    # PDF citations do not reuse the tabular row fields.
    assert "row_start" not in citation
    assert "sheet_name" not in citation


def test_plain_pdf_pages_become_ready_with_page_verifiable_citations(t060_stack) -> None:
    stack = t060_stack
    alice, base_id = setup_user(stack)
    content = t060.fixture("t060_plain.pdf")
    reference = t060.reference()["fixtures"]["t060_plain.pdf"]
    assert t060.sha256_hex(content) == reference["sha256"]

    artifact_id = t060.upload_source(stack, alice, content)
    # The real finalize guard recorded an accepted admission for exactly these
    # bytes: purpose=knowledge admits application/pdf and the sniffed format is pdf.
    admission = t060.admission_row(stack.dbname, artifact_id)
    assert admission["declared_media_type"] == "application/pdf", admission
    assert admission["sniffed_media_type"] == "application/pdf", admission
    assert admission["detected_format"] == "pdf", admission
    assert admission["verdict"] == "accepted", admission

    document_id, view = t060.ingest_and_wait(
        stack, alice, base_id, artifact_id, "maintenance-t060-plain.log"
    )
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    row = t055.document_row(stack.dbname, document_id)
    assert row["parser_version"] == t060.PARSER, row
    assert row["chunker_version"] == t060.CHUNKER, row
    assert row["embedding_model_revision"] == t060.EMBEDDING_REVISION, row
    assert row["error_code"] is None, row

    source = t055.artifact_row(stack.dbname, artifact_id)
    sealed = t060.sealed_source_bytes(stack, source)
    assert t060.sha256_hex(sealed) == source["sha256"] == reference["sha256"]
    pages = reference["pages"]
    expected_text = "\n\n".join(pages)

    chunks = t055.chunk_rows(stack.dbname, document_id, 1)
    assert chunks, chunks
    assert [int(chunk["chunk_no"]) for chunk in chunks] == list(range(len(chunks)))
    covered_pages: set[int] = set()
    for chunk in chunks:
        assert int(chunk["embedding_length"]) == t060.DIMENSION
        assert chunk["embedding_model_revision"] == t060.EMBEDDING_REVISION
        body = t060.chunk_body_text(stack, chunk)
        citation = t055.citation_of(chunk)
        assert citation["source_artifact_id"] == artifact_id
        assert citation["source_sha256"] == reference["sha256"]
        assert citation["source_media_type"] == "application/pdf"
        assert citation["parser_version"] == t060.PARSER
        assert citation["chunker_version"] == t060.CHUNKER
        assert citation["text_normalization"] == "none"
        assert citation["text_render"] == t060.TEXT_RENDER
        assert citation["offset_unit"] == "rune"
        assert_pdf_citation_shape(citation)
        pdf = citation["pdf"]
        assert pdf["page_count"] == reference["page_count"]
        assert pdf["page_start"] == pdf["page_end"]
        page_number = int(pdf["page_start"])
        covered_pages.add(page_number)
        assert 1 <= page_number <= reference["page_count"]

        # Independent truth 1: the committed page text slice by the page-local
        # offsets is exactly the sealed chunk body.
        page_text = pages[page_number - 1]
        page_start = int(pdf["page_char_start"])
        page_end = int(pdf["page_char_end"])
        assert 0 <= page_start < page_end <= len(page_text), (citation, len(page_text))
        assert page_text[page_start:page_end] == body
        # Independent truth 2: the global rune span slices the page concatenation
        # built from the reference pages, and its line numbers agree.
        start = int(citation["span"]["start_char"])
        end = int(citation["span"]["end_char"])
        assert expected_text[start:end] == body
        assert int(citation["span"]["start_line"]) == expected_text[:start].count("\n") + 1
        assert int(citation["span"]["end_line"]) == expected_text[: end - 1].count("\n") + 1
        assert body.strip()
        assert "pages_without_text" not in pdf
    assert covered_pages == {1, 2, 3}, covered_pages

    # Retrieval and citation opening use the same protocol as txt/md: the item
    # carries the stored citation and opening it re-checks the current revision.
    retrieved = t055.retrieval_query(alice, base_id, "Quarterly sales refund", top_k=20)
    assert retrieved.status == 200, retrieved.text
    items = retrieved.json["items"]
    assert items, retrieved.json
    by_id = {chunk["id"]: chunk for chunk in chunks}
    for item in items:
        assert item["chunk_id"] in by_id
        assert item["citation"] == t055.citation_of(by_id[item["chunk_id"]])
        assert item["citation"]["pdf"]["page_start"] >= 1
        assert item["text"] == t060.chunk_body_text(stack, by_id[item["chunk_id"]])
    opened = t055.get_citation(alice, chunks[0]["id"])
    assert opened.status == 200, opened.text
    assert opened.json["citation"] == t055.citation_of(chunks[0])
    assert opened.json["text"] == t060.chunk_body_text(stack, chunks[0])


def test_a_partial_text_layer_records_the_image_only_page_honestly(t060_stack) -> None:
    stack = t060_stack
    alice, base_id = setup_user(stack)
    content = t060.fixture("t060_partial.pdf")
    reference = t060.reference()["fixtures"]["t060_partial.pdf"]
    assert reference["pages_without_text"] == [2]

    artifact_id, document_id = ingest_ready(
        stack, alice, base_id, content, "maintenance-t060-partial.log"
    )
    assert t055.document_row(stack.dbname, document_id)["error_code"] is None

    chunks = t055.chunk_rows(stack.dbname, document_id, 1)
    assert chunks, chunks
    pages = reference["pages"]
    for chunk in chunks:
        body = t060.chunk_body_text(stack, chunk)
        citation = t055.citation_of(chunk)
        pdf = citation["pdf"]
        assert pdf["page_count"] == 3, citation
        assert pdf["pages_without_text"] == [2], citation
        assert pdf["page_start"] in (1, 3), citation
        assert pages[pdf["page_start"] - 1][
            int(pdf["page_char_start"]):int(pdf["page_char_end"])
        ] == body
        # The empty page has no chunk, so no chunk body can claim its content.
        assert pdf["page_start"] != 2
    assert {int(t055.citation_of(chunk)["pdf"]["page_start"]) for chunk in chunks} == {1, 3}
    # The document is searchable because two pages carry real text, but the
    # citation states that page 2 has no text layer instead of implying coverage.
    retrieved = t055.retrieval_query(alice, base_id, "archive continuation", top_k=20)
    assert retrieved.status == 200, retrieved.text
    assert retrieved.json["items"]
    assert {item["citation"]["pdf"]["pages_without_text"][0] for item in retrieved.json["items"]} == {2}


def test_scanned_and_short_pdfs_fail_as_pdf_no_text_layer_never_ready(t060_stack) -> None:
    stack = t060_stack
    alice, base_id = setup_user(stack)
    cases = {
        "scanned": t060.fixture("t060_scanned.pdf"),
        "short": t060.fixture("t060_short.pdf"),
    }
    artifacts = {
        label: t060.upload_source(stack, alice, content)
        for label, content in cases.items()
    }
    # Both bytes are admitted PDFs: the refusal comes from the text-layer rule,
    # not from upload admission. This is the "scanned files cannot masquerade as
    # OCR success" boundary.
    for label, artifact_id in artifacts.items():
        admission = t060.admission_row(stack.dbname, artifact_id)
        assert admission["verdict"] == "accepted", (label, admission)
        assert admission["detected_format"] == "pdf", (label, admission)

    views = t060.ingest_many(stack, alice, base_id, list(artifacts.items()))
    for label, view in views.items():
        assert view["state"] == "failed", (label, view)
        diagnostics = t060.assert_failed_document(
            stack, alice, base_id, view["id"], "pdf_no_text_layer"
        )
        detail = diagnostics["detail"] or {}
        assert detail.get("part") == "text_layer", (label, diagnostics)
        assert detail.get("limit") == "min_text_runes=32", (label, diagnostics)
        if label == "scanned":
            # No characters at all: no OCR was attempted, no chunk was written.
            assert "value" not in detail, (label, diagnostics)
        else:
            assert int(detail["value"]) == t060.non_whitespace_runes(
                t060.reference()["fixtures"]["t060_short.pdf"]["pages"][0]
            ), (label, diagnostics)
            assert int(detail["value"]) < t060.MIN_TEXT_RUNES
        assert t055.chunk_count(stack.dbname, view["id"]) == 0

    # Re-ingesting the same scanned bytes is an explicit retry that stays failed:
    # the refusal is deterministic, not a transient OCR opportunity.
    scanned_document = views["scanned"]["id"]
    again = t055.ingest_document(alice, base_id, artifacts["scanned"])
    assert again.status == 202, again.text
    assert again.json["id"] == scanned_document
    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-t060-scan-retry.log"
    ):
        retried = t055.wait_for_document_state(
            alice, scanned_document, {"failed"}, timeout=60.0
        )
    assert int(retried["revision"]) == 2, retried
    assert t055.document_row(stack.dbname, scanned_document)["error_code"] == "pdf_no_text_layer"

    # Recovery is a new artifact with a real text layer: the failed document never
    # blocks the base and never becomes ready itself.
    fixed_artifact, fixed_document = ingest_ready(
        stack, alice, base_id, t060.fixture("t060_plain.pdf"),
        "maintenance-t060-scan-recovery.log",
    )
    assert fixed_artifact != artifacts["scanned"]
    assert fixed_document != scanned_document
    assert t055.document_row(stack.dbname, scanned_document)["state"] == "failed"
    recovered = t055.retrieval_query(alice, base_id, "Quarterly sales", top_k=20)
    assert recovered.status == 200, recovered.text
    assert {item["document_id"] for item in recovered.json["items"]} == {fixed_document}


def test_invalid_and_encrypted_pdfs_fail_with_their_own_codes(t060_stack) -> None:
    stack = t060_stack
    alice, base_id = setup_user(stack)
    cases = {
        "invalid": (t060.invalid_pdf(), "pdf_invalid"),
        "encrypted": (t060.encrypted_pdf(), "pdf_encrypted"),
    }
    artifacts = {
        label: t060.upload_source(stack, alice, content, seed_ready=True)
        for label, (content, _code) in cases.items()
    }
    views = t060.ingest_many(stack, alice, base_id, list(artifacts.items()))
    for label, (content, code) in cases.items():
        view = views[label]
        assert view["state"] == "failed", (label, view)
        diagnostics = t060.assert_failed_document(stack, alice, base_id, view["id"], code)
        detail = diagnostics["detail"] or {}
        assert detail.get("part") in {"header", "xref", "structure", "trailer"}, (
            label, diagnostics,
        )
        assert t055.chunk_count(stack.dbname, view["id"]) == 0


def test_pdf_chunks_use_the_same_acl_and_authorization_protocol_as_text(t060_stack) -> None:
    stack = t060_stack
    admin = f00b.admin_session(stack)
    alice = f00b.invite_and_activate(stack, admin, "t060.acl", password=f00b.USER_PASSWORD)
    bob = f00b.invite_and_activate(stack, admin, "t060.bob", password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, "t060 acl").json["id"]

    sources = {
        "pdf": (t060.fixture("t060_plain.pdf"), t060.PDF),
        "txt": (t055.SAMPLE_DOCUMENT, "text/plain"),
    }
    artifacts = {
        label: t060.upload_source(stack, alice, content, media_type=media_type)
        for label, (content, media_type) in sources.items()
    }
    views = t060.ingest_many(stack, alice, base_id, list(artifacts.items()))
    documents = {label: views[label]["id"] for label in sources}
    assert {label: views[label]["state"] for label in sources} == {
        "pdf": "ready", "txt": "ready",
    }, views

    retrieved = t055.retrieval_query(alice, base_id, "sales policy", top_k=20)
    assert retrieved.status == 200, retrieved.text
    items = retrieved.json["items"]
    assert items
    assert {item["document_id"] for item in items} == set(documents.values()), items
    pdf_items = [item for item in items if item["document_id"] == documents["pdf"]]
    assert pdf_items
    for item in pdf_items:
        assert item["citation"]["pdf"]["page_start"] >= 1
        assert len(item["citation"]["source_sha256"]) == 64

    # A foreign principal and an administrator get exactly the same 404 on the
    # PDF retrieval and every PDF citation: one ACL protocol, no format exemption.
    sample = [item["chunk_id"] for item in pdf_items[:3]]
    for caller in (bob, admin):
        assert t055.retrieval_query(caller, base_id, "sales policy").status == 404
        for chunk_id in sample:
            response = t055.get_citation(caller, chunk_id)
            assert response.status == 404, response.text
            assert response.json["error"]["code"] == "resource_not_found"
        for document_id in documents.values():
            assert t055.get_document(caller, document_id).status == 404

    # Revoking the PDF document immediately removes its chunks from retrieval and
    # makes every PDF citation unavailable, while the txt document stays readable.
    t055.st.sql(
        stack.dbname,
        "UPDATE platform.knowledge_documents SET state = 'deleted', deleted_at = now() "
        f"WHERE id = {t055.st.quote_literal(documents['pdf'])}::uuid",
    )
    after = t055.retrieval_query(alice, base_id, "sales policy", top_k=20)
    assert after.status == 200, after.text
    assert documents["pdf"] not in {item["document_id"] for item in after.json["items"]}
    assert {item["document_id"] for item in after.json["items"]} == {documents["txt"]}
    for chunk_id in sample:
        revoked = t055.get_citation(alice, chunk_id)
        assert revoked.status == 404, revoked.text
        assert revoked.json["error"]["code"] == "resource_not_found"


def test_pdf_upload_admission_is_knowledge_scoped_and_sniffs_the_bytes(t060_stack) -> None:
    stack = t060_stack
    admin = f00b.admin_session(stack)
    alice = f00b.invite_and_activate(stack, admin, "t060.upload", password=f00b.USER_PASSWORD)

    # 1. purpose=task_input does not admit application/pdf at declaration time.
    refused = t028.create_upload(
        alice, media_type="application/pdf", size_bytes=64, purpose="task_input"
    )
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == "validation_failed"
    assert refused.json["error"]["details"]["field"] == "media_type"

    # 2. purpose=knowledge admits the declaration, but the real bytes must start
    # with %PDF-: a declared PDF that is text is quarantined, never parsed.
    body = b"not a pdf at all, just text pretending to be one\n"
    ticket = t028.create_upload(
        alice, media_type="application/pdf", size_bytes=len(body), purpose="knowledge"
    )
    assert ticket.status == 201, ticket.text
    put = t055.put_link(ticket.json["upload_url"], body)
    assert put.status == 204, put.status
    completed = t028.complete_upload(
        alice, ticket.json["artifact_id"], t060.sha256_hex(body)
    )
    assert completed.status == 202, completed.text
    t055.finalize_once(stack)
    state = t055.wait_for_artifact_state(
        alice, ticket.json["artifact_id"], {"quarantined"}, timeout=30.0
    )
    assert state["state"] == "quarantined", state
    admission = t060.admission_row(stack.dbname, ticket.json["artifact_id"])
    assert admission["declared_media_type"] == "application/pdf", admission
    assert admission["verdict"] == "rejected", admission
    assert admission["reason"] == "content_type_mismatch", admission
