"""F10/T059 real-stack helpers: CSV/XLSX ingestion with row ranges.

The stack is the T055 one: a throwaway PostgreSQL database migrated by the real
``cmd/admin migrate`` chain, the compiled ``cmd/api`` with the real filesystem
object store and signed links, and a real ``cmd/maintenance`` process running the
real ``document_ingest`` and ``artifact_finalize`` handlers.

Nothing here mocks the pipeline under test. The independent truth readers below
parse the *sealed source bytes* with Python's standard library (``csv``,
``zipfile``, ``ElementTree``) and are deliberately written separately from the Go
parser: a test that read the platform's own answer back would prove nothing.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from support import t055
from support import stack as st

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "f10"

CSV = "text/csv"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PURPOSE_KNOWLEDGE = "knowledge"

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


# ---------------------------------------------------------------------------
# committed fixtures
# ---------------------------------------------------------------------------
def fixture_csv() -> bytes:
    """The committed CSV export (UTF-8 BOM, CRLF, quoted comma/quote/newline)."""
    return (FIXTURES / "t059_sales.csv").read_bytes()


def fixture_xlsx() -> bytes:
    """The committed workbook: two sheets, shared strings, a row-number gap."""
    return (FIXTURES / "t059_workbook.xlsx").read_bytes()


# ---------------------------------------------------------------------------
# the real upload -> finalize -> ready path
# ---------------------------------------------------------------------------
def upload_source(
    stack,
    session,
    content: bytes,
    media_type: str,
    *,
    seed_ready: bool = False,
) -> str:
    """Upload+finalize one source artifact, or seed a real ready artifact.

    Seeding is used only for content the T028 admission gate would quarantine
    (invalid CSV quoting, a broken zip, a macro part, an entry-count bomb): those
    bytes must reach the *parser* to prove the parser's own refusal. The row and
    the sealed bytes are real in both paths.
    """
    if seed_ready:
        return t055.seed_ready_source_artifact(
            stack, session.principal_id, content, media_type=media_type
        )
    return t055.prepare_source(stack, session, content, media_type=media_type)


def admission_row(dbname: str, artifact_id: str) -> dict:
    """The real admission fact the finalize guard recorded for one artifact."""
    rows = st.sql_rows(
        dbname,
        "SELECT artifact_id::text AS artifact_id, declared_media_type, sniffed_media_type, "
        "detected_format, verdict, reason "
        "FROM platform.artifact_admissions "
        f"WHERE artifact_id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def ingest_and_wait(
    stack,
    session,
    base_id: str,
    artifact_id: str,
    log_name: str,
    *,
    timeout: float = 90.0,
) -> tuple[str, dict]:
    """Accept one ingestDocument and wait for a terminal document state."""
    with t055.ingest_pools(stack, stack.server.log_path.parent / log_name):
        accepted = t055.ingest_document(session, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(
            session, document_id, {"ready", "failed"}, timeout=timeout
        )
    return document_id, view


def ingest_many(stack, session, base_id: str, artifacts: list[tuple[str, str]]) -> dict[str, dict]:
    """Ingest several artifacts inside one real maintenance process.

    ``artifacts`` is [(label, artifact_id)]; the return maps label -> document view.
    One process (and one wait per document) keeps the suite's wall-clock bounded
    without weakening any assertion: every document still runs the real handler.
    """
    views: dict[str, dict] = {}
    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t059-batch.log"):
        documents: dict[str, str] = {}
        for label, artifact_id in artifacts:
            accepted = t055.ingest_document(session, base_id, artifact_id)
            assert accepted.status == 202, (label, accepted.text)
            documents[label] = accepted.json["id"]
        for label, document_id in documents.items():
            views[label] = t055.wait_for_document_state(
                session, document_id, {"ready", "failed"}, timeout=90.0
            )
    return views


def assert_failed_document(
    stack,
    session,
    base_id: str,
    document_id: str,
    code: str,
) -> dict:
    """Assert a content refusal is a terminal, non-retryable failed document."""
    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "failed", row
    assert row["error_code"] == code, row
    assert row["active_revision"] is None, row
    assert t055.chunk_count(stack.dbname, document_id) == 0, row
    jobs = t055.document_jobs(stack.dbname, document_id)
    assert jobs, jobs
    assert jobs[-1]["state"] == "failed", jobs
    assert jobs[-1]["error_code"] == code, jobs
    assert jobs[-1]["failure_class"] == "business", jobs
    diagnostics = t055.document_diagnostics(session, document_id)
    assert diagnostics.status == 200, diagnostics.text
    assert diagnostics.json["error_code"] == code, diagnostics.json
    assert diagnostics.json["chunk_count"] == 0, diagnostics.json
    # A failed document is not searchable: an empty answer, not an error.
    queried = t055.retrieval_query(session, base_id, "任何内容")
    assert queried.status == 200, queried.text
    assert queried.json["items"] == []
    return diagnostics.json


# ---------------------------------------------------------------------------
# independent truth: the decoded CSV text and its records
# ---------------------------------------------------------------------------
def csv_text(content: bytes) -> str:
    """The parser's documented decode: exactly one leading UTF-8 BOM stripped."""
    text = content.decode("utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text


def csv_records(content: bytes) -> list[list[str]]:
    """The source's logical CSV records, blank lines removed (independent)."""
    reader = csv.reader(io.StringIO(csv_text(content), newline=""), strict=True)
    return [record for record in reader if record]


# ---------------------------------------------------------------------------
# independent truth: the first XLSX worksheet
# ---------------------------------------------------------------------------
def _column_index(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha()).upper()
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values = []
    for item in root.iter():
        if item.tag != "{%s}si" % MAIN_NS:
            continue
        values.append(
            "".join(
                child.text or ""
                for child in item.iter()
                if child.tag == "{%s}t" % MAIN_NS
            )
        )
    return values


def xlsx_first_sheet(content: bytes) -> tuple[str, list[tuple[int, list[str]]]]:
    """Return (sheet name, [(worksheet row number, cell texts)]).

    This is a reader written from the OOXML parts, not from the platform's
    parser: it is the oracle the citation's sheet name and row numbers are
    checked against. Rows whose cells are all empty are skipped (formatting
    residue), matching what a table is.
    """
    archive = zipfile.ZipFile(io.BytesIO(content))
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    sheet_element = next(
        element for element in workbook.iter() if element.tag == "{%s}sheet" % MAIN_NS
    )
    sheet_name = sheet_element.get("name")
    relationship_id = sheet_element.get("{%s}id" % REL_NS)
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(
        element.get("Target")
        for element in relationships.iter()
        if element.tag.endswith("Relationship") and element.get("Id") == relationship_id
    )
    path = target[1:] if target.startswith("/") else "xl/" + target
    shared = _shared_strings(archive)
    worksheet = ElementTree.fromstring(archive.read(path))

    rows: list[tuple[int, list[str]]] = []
    for row in worksheet.iter():
        if row.tag != "{%s}row" % MAIN_NS:
            continue
        number = int(row.get("r") or len(rows) + 1)
        values: dict[int, str] = {}
        position = 0
        for cell in row:
            if cell.tag != "{%s}c" % MAIN_NS:
                continue
            reference = cell.get("r") or ""
            index = _column_index(reference) if reference else position
            position = index + 1
            kind = cell.get("t", "n")
            raw = ""
            for child in cell:
                if child.tag == "{%s}v" % MAIN_NS:
                    raw = child.text or ""
            if kind == "s":
                text = shared[int(raw)]
            elif kind == "inlineStr":
                inline = next(
                    (child for child in cell if child.tag == "{%s}is" % MAIN_NS), None
                )
                text = "".join(
                    child.text or ""
                    for child in (inline.iter() if inline is not None else [])
                    if child.tag == "{%s}t" % MAIN_NS
                )
            else:
                text = raw
            values[index] = text
        if not values or all(value == "" for value in values.values()):
            continue
        rows.append((number, [values.get(index, "") for index in range(max(values) + 1)]))
    return sheet_name, rows


# ---------------------------------------------------------------------------
# generated content refusals (formula, caps, broken archives)
# ---------------------------------------------------------------------------
def _xlsx_archive(entries: dict[str, bytes], *, pad_bytes: int = 0) -> bytes:
    if pad_bytes:
        source = random.Random(1)
        entries = dict(entries)
        entries["xl/media/padding.bin"] = bytes(
            source.randrange(256) for _ in range(pad_bytes)
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def _xlsx_shell(*sheets: str, sheet_xml: str, workbook_extra: str = "") -> dict[str, bytes]:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Types xmlns="{CONTENT_TYPES_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        f'ContentType="{XLSX}.main+xml"/>'
        "</Types>"
    ).encode("utf-8")
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}"><sheets>'
        + "".join(
            f'<sheet name="{name}" sheetId="{index + 1}" r:id="rId{index + 1}"/>'
            for index, name in enumerate(sheets)
        )
        + "</sheets>"
        + workbook_extra
        + "</workbook>"
    ).encode("utf-8")
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        + "".join(
            f'<Relationship Id="rId{index + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index + 1}.xml"/>'
            for index in range(len(sheets))
        )
        + "</Relationships>"
    ).encode("utf-8")
    return {
        "[Content_Types].xml": content_types,
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{PKG_REL_NS}"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'
        ).encode("utf-8"),
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": relationships,
        "xl/worksheets/sheet1.xml": sheet_xml.encode("utf-8"),
    }


def _sheet_data(rows: list[tuple[int, str]]) -> str:
    body = "".join(f'<row r="{number}">{cells}</row>' for number, cells in rows)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{MAIN_NS}"><sheetData>{body}</sheetData></worksheet>'
    )


def _inline(reference: str, text: str) -> str:
    return f'<c r="{reference}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def _header_row(names: list[str]) -> str:
    return "".join(_inline(_cell_ref(index, 1), name) for index, name in enumerate(names))


def _cell_ref(index: int, row: int) -> str:
    letters = ""
    value = index + 1
    while value > 0:
        value -= 1
        letters = chr(ord("A") + value % 26) + letters
        value //= 26
    return f"{letters}{row}"


def formula_xlsx() -> bytes:
    """A workbook whose B2 cell carries a formula with a cached value."""
    rows = [
        (1, _header_row(["order_id", "amount"])),
        (
            2,
            _inline("A2", "A1")
            + '<c r="B2"><f>SUM(C2:C3)</f><v>999</v></c>',
        ),
    ]
    entries = _xlsx_shell("Orders", sheet_xml=_sheet_data(rows))
    return _xlsx_archive(entries)


def dtd_xlsx() -> bytes:
    """A workbook that declares a DTD/entity (admission reads only the name)."""
    entries = _xlsx_shell(
        "Orders",
        sheet_xml=_sheet_data([(1, _header_row(["a"])), (2, _inline("A2", "x"))]),
        workbook_extra="",
    )
    entries["xl/workbook.xml"] = (
        b'<?xml version="1.0"?><!DOCTYPE workbook [<!ENTITY boom "x">]>'
        b'<workbook xmlns="' + MAIN_NS.encode() + b'"><sheets>'
        b'<sheet name="Orders" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    return _xlsx_archive(entries)


def macro_xlsx() -> bytes:
    """A workbook that also carries a macro part (admission would quarantine)."""
    entries = _xlsx_shell(
        "Orders",
        sheet_xml=_sheet_data([(1, _header_row(["a"])), (2, _inline("A2", "x"))]),
    )
    entries["xl/vbaProject.bin"] = b"macro bytes"
    return _xlsx_archive(entries)


def broken_zip() -> bytes:
    return b"PK\x03\x04this is not a readable zip central directory"


def ratio_bomb_xlsx() -> bytes:
    """One 1 MiB cell of one repeated character: a real compression bomb."""
    rows = [
        (1, _header_row(["a"])),
        (2, _inline("A2", "x" * (1 << 20))),
    ]
    return _xlsx_archive(_xlsx_shell("Bomb", sheet_xml=_sheet_data(rows)))


def many_rows_xlsx(count: int = 10001) -> bytes:
    rows = [(1, _header_row(["a", "b"]))]
    for index in range(count):
        number = index + 2
        rows.append((number, _inline(_cell_ref(0, number), "x") + _inline(_cell_ref(1, number), "y")))
    # Padding keeps the archive ratio under the bomb ceiling so the *row* cap is
    # the refusal under test (a 10k-row sheet of real text does not compress 100x).
    return _xlsx_archive(_xlsx_shell("Rows", sheet_xml=_sheet_data(rows)), pad_bytes=1 << 20)


def many_columns_xlsx(count: int = 65) -> bytes:
    cells = "".join(_inline(_cell_ref(index, 1), "c") for index in range(count))
    return _xlsx_archive(_xlsx_shell("Columns", sheet_xml=_sheet_data([(1, cells)])))


def many_worksheets_xlsx(count: int = 65) -> bytes:
    entries = _xlsx_shell(
        *(f"S{index}" for index in range(count)),
        sheet_xml=_sheet_data([(1, _header_row(["a"])), (2, _inline("A2", "x"))]),
    )
    return _xlsx_archive(entries)


def row_too_large_xlsx() -> bytes:
    """One data row longer than the row-character cap, with incompressible text."""
    source = random.Random(7)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    noise = "".join(alphabet[source.randrange(len(alphabet))] for _ in range(65537))
    rows = [
        (1, _header_row(["note"])),
        (2, _inline("A2", noise)),
    ]
    return _xlsx_archive(_xlsx_shell("Long", sheet_xml=_sheet_data(rows)))


def invalid_csv() -> bytes:
    """An unterminated quoted field on row 2 (admission would quarantine it)."""
    return b'order_id,note\r\nA1,"unterminated\r\n'


def empty_csv() -> bytes:
    """A header without data rows: valid CSV, but nothing to index."""
    return b"order_id,note\r\n"


# ---------------------------------------------------------------------------
# chunk/citation truth helpers
# ---------------------------------------------------------------------------
def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sealed_source_bytes(stack, artifact: dict) -> bytes:
    return t055.object_bytes(stack.object_dir, artifact["object_key"])


def chunk_body_text(stack, chunk: dict) -> str:
    body = t055.artifact_row(stack.dbname, chunk["body_artifact_id"])
    return t055.object_bytes(stack.object_dir, body["object_key"]).decode("utf-8")


def chunk_citations(chunks: list[dict]) -> list[dict]:
    return [json.loads(chunk["citation"]) for chunk in chunks]
