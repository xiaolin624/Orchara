#!/usr/bin/env python3
"""Build the committed F10/T059 CSV and XLSX fixtures deterministically.

Run this once and commit the two output files:

    python tests/fixtures/f10/build_fixtures.py

The tests only read the outputs; they never run this script, and they verify the
chunks against the sealed bytes rather than against this generator. The archive
is written with a fixed timestamp, entry order and compression so regenerating it
produces identical bytes (the SHA-256s are recorded in the ticket report).
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
CSV_PATH = HERE / "t059_sales.csv"
XLSX_PATH = HERE / "t059_workbook.xlsx"

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

REGIONS = ("华东", "华北", "华南,区", "西南", "东北")
NOTES = ("首单", "复购", '备注 "加急"', "", "周末订单")


def build_csv() -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(("order_id", "date", "region", "note", "amount"))
    for index in range(1, 61):
        note = NOTES[index % len(NOTES)]
        if index == 7:
            # A quoted field with an embedded line break: the row span must still
            # cover the whole logical record, not just its first physical line.
            note = "多行\n备注"
        writer.writerow(
            (
                f"A{index:03d}",
                f"2026-10-{(index - 1) % 28 + 1:02d}",
                REGIONS[index % len(REGIONS)],
                note,
                f"{100 + index}.50",
            )
        )
    # A UTF-8 BOM and CRLF line endings are the common export shape; the parser
    # records the BOM strip and keeps CRLF, so spans stay honest.
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


class SharedStrings:
    def __init__(self) -> None:
        self._values: list[str] = []
        self._index: dict[str, int] = {}

    def index_of(self, value: str) -> int:
        existing = self._index.get(value)
        if existing is not None:
            return existing
        self._values.append(value)
        position = len(self._values) - 1
        self._index[value] = position
        return position

    def xml(self) -> bytes:
        items = "".join(f"<si><t>{escape(value)}</t></si>" for value in self._values)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="{MAIN_NS}">{items}</sst>'
        ).encode("utf-8")


def _sheet_xml(rows: list[tuple[int, list[tuple[str, str, str]]]]) -> bytes:
    """rows: (worksheet row number, [(cell reference, kind, value)])."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<worksheet xmlns="{MAIN_NS}"><sheetData>',
    ]
    for number, cells in rows:
        parts.append(f'<row r="{number}">')
        for reference, kind, value in cells:
            if kind == "s":
                parts.append(f'<c r="{reference}" t="s"><v>{value}</v></c>')
            elif kind == "n":
                parts.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                parts.append(
                    f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                )
        parts.append("</row>")
    parts.append("</sheetData></worksheet>")
    return "".join(parts).encode("utf-8")


def build_xlsx() -> bytes:
    shared = SharedStrings()

    def text(reference: str, value: str) -> tuple[str, str, str]:
        return (reference, "s", str(shared.index_of(value)))

    def number(reference: str, value: str) -> tuple[str, str, str]:
        return (reference, "n", value)

    header = [
        text("A1", "order_id"),
        text("B1", "date"),
        text("C1", "region"),
        text("D1", "amount"),
    ]
    sales_rows: list[tuple[int, list[tuple[str, str, str]]]] = [(1, header)]
    for index in range(1, 41):
        number_row = index + 1
        sales_rows.append(
            (
                number_row,
                [
                    text(f"A{number_row}", f"A{index:03d}"),
                    text(f"B{number_row}", f"2026-10-{(index - 1) % 28 + 1:02d}"),
                    text(f"C{number_row}", REGIONS[index % len(REGIONS)]),
                    number(f"D{number_row}", f"{100 + index}.50"),
                ],
            )
        )
    # Rows 42 is absent and rows 43-44 continue: the citation's row numbers must
    # be the worksheet's own numbers, not a data-row counter.
    for index, number_row in ((41, 43), (42, 44)):
        sales_rows.append(
            (
                number_row,
                [
                    text(f"A{number_row}", f"A{index:03d}"),
                    text(f"B{number_row}", f"2026-10-{(index - 1) % 28 + 1:02d}"),
                    text(f"C{number_row}", REGIONS[index % len(REGIONS)]),
                    number(f"D{number_row}", f"{100 + index}.50"),
                ],
            )
        )

    notes_rows = [
        (1, [text("A1", "note")]),
        (2, [("A2", "inline", "second sheet must never be ingested")]),
    ]

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{CONTENT_TYPES_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{XLSX_MEDIA_TYPE}.main+xml"/>'
        "</Types>"
    ).encode("utf-8")
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    ).encode("utf-8")
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}"><sheets>'
        '<sheet name="Sales" sheetId="1" r:id="rId1"/>'
        '<sheet name="Notes" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    ).encode("utf-8")
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        "</Relationships>"
    ).encode("utf-8")

    entries = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": package_rels,
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": workbook_rels,
        "xl/sharedStrings.xml": shared.xml(),
        "xl/worksheets/sheet1.xml": _sheet_xml(sales_rows),
        "xl/worksheets/sheet2.xml": _sheet_xml(notes_rows),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


if __name__ == "__main__":
    CSV_PATH.write_bytes(build_csv())
    XLSX_PATH.write_bytes(build_xlsx())
    print(f"wrote {CSV_PATH} ({CSV_PATH.stat().st_size} bytes)")
    print(f"wrote {XLSX_PATH} ({XLSX_PATH.stat().st_size} bytes)")
