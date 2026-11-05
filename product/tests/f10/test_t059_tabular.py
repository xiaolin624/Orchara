"""F10/T059 acceptance: CSV/XLSX ingestion keeps row ranges and safe errors.

Every test drives the public path: the existing artifact upload/finalize flow
(really admitted bytes), one ingestDocument call, a real maintenance process with
the real document_ingest handler, and the SQL-filtered retrieval/citation reads.
Chunk citations are checked against an independent truth parsed from the *sealed
source bytes* (Python's csv/zipfile/ElementTree), never against the platform's own
answer. Formula cells are refused, caps are refused, and every refusal is a
diagnosable failed document, never a ready one.
"""

from __future__ import annotations

import csv
import io

from support import f00b, t055, t059
from support.fixtures import t059_stack  # noqa: F401 - pytest fixture

EMBEDDING_REVISION = "local-hash-v1"
DIMENSION = 384
CSV_PARSER = "orchara-csv-parser/1"
XLSX_PARSER = "orchara-xlsx-parser/1"
TABLE_CHUNKER = "orchara-table-row-batch-chunker/1"
PARAGRAPH_CHUNKER = "orchara-paragraph-overlap-chunker/1"


def setup_user(stack, login: str = "t059.alice"):
    admin = f00b.admin_session(stack)
    alice = f00b.invite_and_activate(stack, admin, login, password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, f"{login} knowledge").json["id"]
    return alice, base_id


def ingest_ready(stack, alice, base_id, content, media_type, log_name):
    """Upload through the real admission path, ingest, and require ready."""
    artifact_id = t059.upload_source(stack, alice, content, media_type)
    document_id, view = t059.ingest_and_wait(stack, alice, base_id, artifact_id, log_name)
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    return artifact_id, document_id


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def test_csv_rows_become_ready_with_row_ranges_verifiable_against_sealed_bytes(
    t059_stack,
) -> None:
    stack = t059_stack
    alice, base_id = setup_user(stack)
    content = t059.fixture_csv()
    artifact_id = t059.upload_source(stack, alice, content, t059.CSV)

    # `purpose=knowledge` already admits text/csv: the real finalize guard
    # recorded an accepted admission for exactly these bytes.
    admission = t059.admission_row(stack.dbname, artifact_id)
    assert admission["declared_media_type"] == "text/csv", admission
    assert admission["sniffed_media_type"] == "text/csv", admission
    assert admission["verdict"] == "accepted", admission

    document_id, view = t059.ingest_and_wait(
        stack, alice, base_id, artifact_id, "maintenance-t059-csv.log"
    )
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    row = t055.document_row(stack.dbname, document_id)
    assert row["parser_version"] == CSV_PARSER, row
    assert row["chunker_version"] == TABLE_CHUNKER, row
    assert row["embedding_model_revision"] == EMBEDDING_REVISION, row
    assert row["error_code"] is None, row

    source = t055.artifact_row(stack.dbname, artifact_id)
    sealed = t059.sealed_source_bytes(stack, source)
    assert t059.sha256_hex(sealed) == source["sha256"], source
    text = t059.csv_text(sealed)
    records = t059.csv_records(sealed)
    assert records[0] == ["order_id", "date", "region", "note", "amount"], records[0]

    chunks = t055.chunk_rows(stack.dbname, document_id, 1)
    assert len(chunks) >= 2, chunks
    assert [int(chunk["chunk_no"]) for chunk in chunks] == list(range(len(chunks)))
    expected_row = 2
    for chunk in chunks:
        assert int(chunk["embedding_length"]) == DIMENSION
        assert chunk["embedding_model_revision"] == EMBEDDING_REVISION
        body = t059.chunk_body_text(stack, chunk)
        citation = t055.citation_of(chunk)
        assert citation["source_artifact_id"] == artifact_id
        assert citation["source_sha256"] == t059.sha256_hex(sealed)
        assert citation["source_media_type"] == "text/csv"
        assert citation["parser_version"] == CSV_PARSER
        assert citation["chunker_version"] == TABLE_CHUNKER
        assert citation["text_normalization"] == "utf8_bom_stripped"
        assert citation["text_render"] == "source_utf8"
        assert citation["offset_unit"] == "rune"
        assert citation["header_row"] == 1
        assert citation["columns"] == records[0]
        assert int(citation["row_start"]) == expected_row
        row_start, row_end = int(citation["row_start"]), int(citation["row_end"])
        assert row_end >= row_start
        assert int(citation["row_count"]) == row_end - row_start + 1

        # The citation locates the rows in the sealed source: the recorded span
        # slices the decoded source to exactly the sealed chunk body, and the
        # body re-parses to exactly the source records in the row range.
        start = int(citation["span"]["start_char"])
        end = int(citation["span"]["end_char"])
        assert text[start:end] == body
        assert int(citation["span"]["start_line"]) == text[:start].count("\n") + 1
        assert int(citation["span"]["end_line"]) == text[: end - 1].count("\n") + 1
        assert list(csv.reader(io.StringIO(body, newline=""), strict=True)) == records[
            row_start - 1: row_end
        ]
        expected_row = row_end + 1
    assert expected_row - 1 == len(records), (expected_row, len(records))

    # Retrieval uses the same citation protocol as txt/md: the item carries the
    # stored citation, and opening it re-checks authorization/current revision.
    retrieved = t055.retrieval_query(alice, base_id, "order_id region amount", top_k=20)
    assert retrieved.status == 200, retrieved.text
    items = retrieved.json["items"]
    assert items, retrieved.json
    by_id = {chunk["id"]: chunk for chunk in chunks}
    for item in items:
        assert item["chunk_id"] in by_id
        assert item["citation"] == t055.citation_of(by_id[item["chunk_id"]])
        citation = item["citation"]
        assert citation["text_render"] == "source_utf8"
        assert int(citation["row_start"]) >= 2
        assert int(citation["row_end"]) >= int(citation["row_start"])
        assert item["text"] == t059.chunk_body_text(stack, by_id[item["chunk_id"]])

    opened = t055.get_citation(alice, chunks[0]["id"])
    assert opened.status == 200, opened.text
    assert opened.json["citation"] == t055.citation_of(chunks[0])
    assert opened.json["text"] == t059.chunk_body_text(stack, chunks[0])


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------
def test_xlsx_chunks_keep_the_sheet_name_and_worksheet_row_numbers(t059_stack) -> None:
    stack = t059_stack
    alice, base_id = setup_user(stack)
    content = t059.fixture_xlsx()
    artifact_id = t059.upload_source(stack, alice, content, t059.XLSX)

    admission = t059.admission_row(stack.dbname, artifact_id)
    assert admission["declared_media_type"] == t059.XLSX, admission
    assert admission["sniffed_media_type"] == t059.XLSX, admission
    assert admission["verdict"] == "accepted", admission

    document_id, view = t059.ingest_and_wait(
        stack, alice, base_id, artifact_id, "maintenance-t059-xlsx.log"
    )
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    row = t055.document_row(stack.dbname, document_id)
    assert row["parser_version"] == XLSX_PARSER, row
    assert row["chunker_version"] == TABLE_CHUNKER, row

    source = t055.artifact_row(stack.dbname, artifact_id)
    sealed = t059.sealed_source_bytes(stack, source)
    sheet_name, independent_rows = t059.xlsx_first_sheet(sealed)
    assert sheet_name == "Sales", sheet_name
    header_number, header_values = independent_rows[0]
    assert header_number == 1
    assert header_values == ["order_id", "date", "region", "amount"], header_values
    data_rows = {int(number): values for number, values in independent_rows[1:]}
    assert min(data_rows) == 2 and max(data_rows) == 44, sorted(data_rows)
    assert 42 not in data_rows, sorted(data_rows)
    assert 43 in data_rows and 44 in data_rows

    chunks = t055.chunk_rows(stack.dbname, document_id, 1)
    assert chunks, chunks
    covered: list[int] = []
    for chunk in chunks:
        body = t059.chunk_body_text(stack, chunk)
        citation = t055.citation_of(chunk)
        assert citation["source_sha256"] == t059.sha256_hex(sealed)
        assert citation["source_media_type"] == t059.XLSX
        assert citation["parser_version"] == XLSX_PARSER
        assert citation["chunker_version"] == TABLE_CHUNKER
        assert citation["text_render"] == "sheet_csv/1", citation
        assert citation["sheet_name"] == "Sales", citation
        assert citation["header_row"] == 1
        assert citation["columns"] == header_values
        row_start, row_end = int(citation["row_start"]), int(citation["row_end"])
        assert row_start >= 2 and row_end >= row_start
        expected = [
            (number, data_rows[number]) for number in range(row_start, row_end + 1)
            if number in data_rows
        ]
        assert expected, citation
        assert expected[0][0] == row_start, (expected[0][0], row_start)
        assert expected[-1][0] == row_end, (expected[-1][0], row_end)
        assert int(citation["row_count"]) == len(expected), citation
        body_records = list(csv.reader(io.StringIO(body, newline=""), strict=True))
        assert body_records == [values for _number, values in expected], citation
        # The span is the truthful range of the rendered sheet text the body
        # holds; the row numbers above locate the same rows in the sealed book.
        assert len(body_records) == int(citation["row_count"])
        covered.extend(number for number, _values in expected)
    assert covered == sorted(data_rows), (covered, sorted(data_rows))
    assert all("second sheet" not in t059.chunk_body_text(stack, chunk) for chunk in chunks)

    retrieved = t055.retrieval_query(alice, base_id, "order_id amount", top_k=20)
    assert retrieved.status == 200, retrieved.text
    items = retrieved.json["items"]
    assert items, retrieved.json
    assert all(item["citation"]["sheet_name"] == "Sales" for item in items)
    assert {item["chunk_id"] for item in items} <= {chunk["id"] for chunk in chunks}
    opened = t055.get_citation(alice, items[0]["chunk_id"])
    assert opened.status == 200, opened.text
    assert opened.json["citation"] == items[0]["citation"]


# ---------------------------------------------------------------------------
# formula refusal + retry/recovery boundary
# ---------------------------------------------------------------------------
def test_formula_cells_are_never_executed_and_the_page_can_diagnose_the_location(
    t059_stack,
) -> None:
    stack = t059_stack
    alice, base_id = setup_user(stack)
    artifact_id = t059.upload_source(stack, alice, t059.formula_xlsx(), t059.XLSX)

    document_id, view = t059.ingest_and_wait(
        stack, alice, base_id, artifact_id, "maintenance-t059-formula.log"
    )
    assert view["state"] == "failed", (view, t055.document_row(stack.dbname, document_id))
    diagnostics = t059.assert_failed_document(
        stack, alice, base_id, document_id, "ingest_formula_not_supported"
    )
    # The failure names the cell: the page can show sheet/row/column without the
    # platform ever evaluating the formula or reading its cached value.
    assert diagnostics["detail"] == {
        "sheet": "Orders", "row": 2, "column": "B", "cell": "B2",
    }, diagnostics
    assert t055.chunk_count(stack.dbname, document_id) == 0

    # Re-ingesting the same bytes is an explicit retry that produces a new
    # revision and a new job; the refusal is deterministic and business, so it
    # fails again instead of being retried into ready.
    again = t055.ingest_document(alice, base_id, artifact_id)
    assert again.status == 202, again.text
    assert again.json["id"] == document_id
    assert again.json["state"] == "scanning"
    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t059-formula-retry.log"):
        retried = t055.wait_for_document_state(alice, document_id, {"failed"}, timeout=60.0)
    assert int(retried["revision"]) == 2, retried
    retried_row = t055.document_row(stack.dbname, document_id)
    assert retried_row["error_code"] == "ingest_formula_not_supported", retried_row
    retried_diagnostics = t055.document_diagnostics(alice, document_id)
    assert retried_diagnostics.json["detail"] == diagnostics["detail"]

    # Recovery is a new artifact with corrected content in the same knowledge
    # base: the failed document never blocks the base and never becomes ready.
    fixed = b"order_id,amount\r\nA1,10.00\r\nA2,20.00\r\n"
    fixed_artifact = t059.upload_source(stack, alice, fixed, t059.CSV)
    fixed_document, fixed_view = t059.ingest_and_wait(
        stack, alice, base_id, fixed_artifact, "maintenance-t059-formula-fixed.log"
    )
    assert fixed_view["state"] == "ready", fixed_view
    assert fixed_document != document_id
    recovered = t055.retrieval_query(alice, base_id, "order_id", top_k=10)
    assert recovered.status == 200, recovered.text
    assert {item["document_id"] for item in recovered.json["items"]} == {fixed_document}


# ---------------------------------------------------------------------------
# content refusals: every one is a failed document with a specific code
# ---------------------------------------------------------------------------
def test_content_refusals_end_as_failed_documents_with_specific_codes(t059_stack) -> None:
    stack = t059_stack
    alice, base_id = setup_user(stack)

    # (label, seed_ready, bytes, media type, expected code, expected detail subset)
    cases = [
        ("invalid_csv", True, t059.invalid_csv(), t059.CSV,
         "ingest_invalid_csv", {"row": 2, "part": "unterminated_quote"}),
        ("broken_xlsx", True, t059.broken_zip(), t059.XLSX,
         "ingest_invalid_xlsx", {"part": "archive"}),
        ("macro_xlsx", True, t059.macro_xlsx(), t059.XLSX,
         "ingest_macro_present", {"part": "macro"}),
        ("dtd_xlsx", False, t059.dtd_xlsx(), t059.XLSX,
         "ingest_dtd_not_supported", {"part": "workbook"}),
        ("ratio_xlsx", False, t059.ratio_bomb_xlsx(), t059.XLSX,
         "ingest_archive_limit_exceeded", {"limit": "compression_ratio"}),
        ("rows_xlsx", False, t059.many_rows_xlsx(), t059.XLSX,
         "ingest_row_limit_exceeded", {"limit": "rows"}),
        ("columns_xlsx", False, t059.many_columns_xlsx(), t059.XLSX,
         "ingest_column_limit_exceeded", {"row": 1, "column": "BM"}),
        ("worksheets_xlsx", False, t059.many_worksheets_xlsx(), t059.XLSX,
         "ingest_worksheet_limit_exceeded", {"limit": "worksheets"}),
        ("row_size_xlsx", False, t059.row_too_large_xlsx(), t059.XLSX,
         "ingest_row_too_large", {"row": 2}),
        ("empty_csv", False, t059.empty_csv(), t059.CSV,
         "ingest_empty_content", None),
    ]
    artifacts = {}
    for label, seed_ready, content, media_type, _code, _detail in cases:
        artifacts[label] = t059.upload_source(
            stack, alice, content, media_type, seed_ready=seed_ready
        )
    views = t059.ingest_many(
        stack, alice, base_id, [(label, artifacts[label]) for label, *_rest in cases]
    )
    for label, _seed, _content, _media, code, detail in cases:
        view = views[label]
        assert view["state"] == "failed", (label, view)
        diagnostics = t059.assert_failed_document(stack, alice, base_id, view["id"], code)
        if detail is None:
            continue
        actual = diagnostics["detail"] or {}
        for key, value in detail.items():
            assert actual.get(key) == value, (label, key, value, actual)


# ---------------------------------------------------------------------------
# different formats, one authorization and citation protocol
# ---------------------------------------------------------------------------
def test_csv_xlsx_and_text_share_the_same_authorization_and_citation_protocol(
    t059_stack,
) -> None:
    stack = t059_stack
    admin = f00b.admin_session(stack)
    alice = f00b.invite_and_activate(stack, admin, "t059.alice", password=f00b.USER_PASSWORD)
    bob = f00b.invite_and_activate(stack, admin, "t059.bob", password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, "t059 acl").json["id"]

    sources = {
        "csv": (t059.fixture_csv(), t059.CSV),
        "xlsx": (t059.fixture_xlsx(), t059.XLSX),
        "txt": (t055.SAMPLE_DOCUMENT, "text/plain"),
    }
    artifacts = {
        label: t059.upload_source(stack, alice, content, media_type)
        for label, (content, media_type) in sources.items()
    }
    views = t059.ingest_many(
        stack, alice, base_id, [(label, artifacts[label]) for label in sources]
    )
    documents = {label: views[label]["id"] for label in sources}
    assert {label: views[label]["state"] for label in sources} == {
        "csv": "ready", "xlsx": "ready", "txt": "ready",
    }, views

    retrieved = t055.retrieval_query(alice, base_id, "order_id amount", top_k=20)
    assert retrieved.status == 200, retrieved.text
    items = retrieved.json["items"]
    assert items
    assert {item["document_id"] for item in items} == set(documents.values()), items
    source_artifact = {
        "csv": artifacts["csv"], "xlsx": artifacts["xlsx"], "txt": artifacts["txt"],
    }
    by_document = {document_id: label for label, document_id in documents.items()}
    for item in items:
        label = by_document[item["document_id"]]
        citation = item["citation"]
        assert citation["document_id"] == item["document_id"]
        assert int(citation["document_revision"]) == int(item["document_revision"])
        assert int(citation["chunk_no"]) == int(item["chunk_no"])
        assert citation["source_artifact_id"] == source_artifact[label]
        assert len(citation["source_sha256"]) == 64
        assert citation["offset_unit"] == "rune"
        if label == "csv":
            assert citation["text_render"] == "source_utf8"
            assert int(citation["row_start"]) >= 2
        elif label == "xlsx":
            assert citation["text_render"] == "sheet_csv/1"
            assert citation["sheet_name"] == "Sales"
            assert int(citation["row_start"]) >= 2
        else:
            assert "row_start" not in citation
            assert "sheet_name" not in citation
            assert "text_render" not in citation
            assert citation["chunker_version"] == PARAGRAPH_CHUNKER

    # A foreign principal and an administrator get exactly the same 404 on every
    # format: one ACL protocol, no format-specific exemption.
    sample = items[: min(3, len(items))]
    for caller in (bob, admin):
        assert t055.retrieval_query(caller, base_id, "order_id").status == 404
        for item in sample:
            response = t055.get_citation(caller, item["chunk_id"])
            assert response.status == 404, response.text
            assert response.json["error"]["code"] == "resource_not_found"
        for document_id in documents.values():
            assert t055.get_document(caller, document_id).status == 404

    # Revoking one format's document immediately removes its chunks from
    # retrieval and its citations from the read path; the others stay readable.
    csv_chunk_ids = {
        item["chunk_id"] for item in items if item["document_id"] == documents["csv"]
    }
    assert csv_chunk_ids
    t055.st.sql(
        stack.dbname,
        "UPDATE platform.knowledge_documents SET state = 'deleted', deleted_at = now() "
        f"WHERE id = {t055.st.quote_literal(documents['csv'])}::uuid",
    )
    after = t055.retrieval_query(alice, base_id, "order_id", top_k=20)
    assert after.status == 200, after.text
    assert documents["csv"] not in {item["document_id"] for item in after.json["items"]}
    assert {item["document_id"] for item in after.json["items"]} == {
        documents["xlsx"], documents["txt"],
    }
    for chunk_id in csv_chunk_ids:
        revoked = t055.get_citation(alice, chunk_id)
        assert revoked.status == 404, revoked.text
        assert revoked.json["error"]["code"] == "resource_not_found"
