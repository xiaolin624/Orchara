#!/usr/bin/env python3
"""Build the committed F10/T060 PDF fixtures deterministically.

Run this once and commit the outputs:

    python tests/fixtures/f10/build_pdf_fixtures.py

The tests only read the outputs; they never run this script. The PDFs are written
by hand (stdlib only) with a classic cross-reference table, Flate-compressed
content streams, a Helvetica WinAnsi font with explicit widths, and no producer
date, so regenerating them on the same Python version produces identical bytes.
The SHA-256 of every output plus its page texts are recorded in
``t060_reference.json``.

The reference page texts are the *authored* page text, not a platform parse. They
are what a reader sees on the rendered page (hand-checked against a page render
during T060); the comparison harness measures each candidate against them.

Fixtures:
  t060_plain.pdf    3 pages of ordinary prose (Flate content, single page stream)
  t060_layout.pdf   1 page with a table-like column layout (three positions per row)
  t060_scanned.pdf  1 page that is one full-page image XObject and no text operators
  t060_partial.pdf  3 pages: page 2 is image-only, pages 1 and 3 have text
  t060_short.pdf    1 page with a text layer below the documented character threshold
"""

from __future__ import annotations

import hashlib
import json
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE_PATH = HERE / "t060_reference.json"

PDF_MEDIA_TYPE = "application/pdf"
MIN_TEXT_RUNES = 32

# Helvetica advance widths /1000 for WinAnsi codes 32..126. Exact AFM values are
# not load-bearing: the widths position glyphs, they are not read back as
# expected text. These are the standard widths for the ASCII range.
_WIDTHS = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]

PLAIN_PAGES = [
    [
        "Quarterly Sales Report",
        "Prepared for the regional operations review.",
        "",
        "North region revenue reached 1234.50 yuan across 42 orders.",
        "East region revenue reached 987.25 yuan across 31 orders.",
    ],
    [
        "Refund policy",
        "A refund may be requested within 30 days after the order is completed.",
        "Requests after that window are not accepted.",
    ],
    [
        "Operations notes",
        "The October close is scheduled for the first business day of November.",
        "All figures in this report are stated in yuan.",
    ],
]

LAYOUT_ROWS = [
    ("Order ID", "Region", "Amount"),
    ("A-1001", "North", "120.50"),
    ("A-1002", "East", "987.25"),
    ("A-1003", "South", "450.00"),
    ("A-1004", "West", "88.10"),
]
LAYOUT_COLUMNS = (72, 220, 380)

PARTIAL_PAGES = [
    [
        "Archive cover sheet",
        "This document was assembled from three source pages.",
    ],
    [],
    [
        "Archive continuation",
        "The middle page is kept as an image because its text layer is missing.",
    ],
]

SHORT_PAGE = ["Scan from the office copier"]

# Hand-checked page text of each fixture (the authored page as a reader sees it).
# A blank line is a paragraph break: the plain fixture's first page leaves one
# visible gap between the report header block and the regional results.
PLAIN_REFERENCE = [
    "Quarterly Sales Report\n"
    "Prepared for the regional operations review.\n"
    "\n"
    "North region revenue reached 1234.50 yuan across 42 orders.\n"
    "East region revenue reached 987.25 yuan across 31 orders.",
    "Refund policy\n"
    "A refund may be requested within 30 days after the order is completed.\n"
    "Requests after that window are not accepted.",
    "Operations notes\n"
    "The October close is scheduled for the first business day of November.\n"
    "All figures in this report are stated in yuan.",
]


def _escape(text: str) -> bytes:
    return (
        text.replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
        .encode("latin-1")
    )


def _line_content(lines: list[str]) -> bytes:
    """One page's content stream: each line starts at 72pt, 14pt leading."""
    parts = [b"BT\n/F1 11 Tf\n14 TL\n1 0 0 1 72 720 Tm\n"]
    for line in lines:
        if line:
            parts.append(b"(" + _escape(line) + b") Tj\nT*\n")
        else:
            parts.append(b"T*\n")
    parts.append(b"ET\n")
    return b"".join(parts)


def _layout_content() -> bytes:
    parts = [b"BT\n/F1 10 Tf\n"]
    y = 720
    for row in LAYOUT_ROWS:
        for x, cell in zip(LAYOUT_COLUMNS, row):
            parts.append(f"1 0 0 1 {x} {y} Tm\n".encode("ascii"))
            parts.append(b"(" + _escape(cell) + b") Tj\n")
        y -= 16
    parts.append(b"ET\n")
    return b"".join(parts)


def _scan_image_bytes() -> tuple[bytes, int, int]:
    """A deterministic 300x400 gray gradient with darker scan bands."""
    width, height = 300, 400
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            value = 190 + (x * 40) // width
            if (y // 24) % 7 == 0 and (x // 12) % 2 == 0:
                value -= 60
            row.append(max(0, min(255, value)))
        rows.append(bytes(row))
    return zlib.compress(b"".join(rows), 9), width, height


def _stream_object(dictionary: str, payload: bytes) -> bytes:
    return (
        f"<< {dictionary} /Length {len(payload)} >>\nstream\n".encode("ascii")
        + payload
        + b"\nendstream"
    )


def build_pdf(
    page_texts: list[list[str]],
    *,
    scan_pages: set[int] | None = None,
    layout: bool = False,
) -> bytes:
    """Build one classic-xref PDF.

    ``scan_pages`` holds 1-based page numbers rendered as a full-page image with
    no text operators. ``layout`` renders the single table-like page.
    """
    scan_pages = scan_pages or set()
    has_scan = bool(scan_pages)
    image_payload, image_width, image_height = _scan_image_bytes()
    font_number = 3
    image_number = 4 if has_scan else None
    first_page_number = 5 if has_scan else 4

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
    }
    page_numbers = [first_page_number + index * 2 for index in range(len(page_texts))]
    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>".encode("ascii")
    )
    objects[font_number] = (
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        "/Encoding /WinAnsiEncoding /FirstChar 32 /LastChar 126 /Widths ["
        + " ".join(str(width) for width in _WIDTHS)
        + "] >>"
    ).encode("ascii")
    if image_number is not None:
        objects[image_number] = _stream_object(
            "/Type /XObject /Subtype /Image "
            f"/Width {image_width} /Height {image_height} "
            "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode",
            image_payload,
        )

    for index, lines in enumerate(page_texts):
        number = page_numbers[index]
        content_number = number + 1
        scan = (index + 1) in scan_pages
        xobjects = (
            f" /XObject << /Im1 {image_number} 0 R >>" if scan and image_number else ""
        )
        objects[number] = (
            "<< /Type /Page /Parent 2 0 R "
            "/MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_number} 0 R >>{xobjects} >> "
            f"/Contents {content_number} 0 R >>"
        ).encode("ascii")
        if scan:
            payload = "q\n612 0 0 792 0 0 cm\n/Im1 Do\nQ\n".encode("ascii")
        elif layout:
            payload = _layout_content()
        else:
            payload = _line_content(lines)
        objects[content_number] = _stream_object(
            "/Filter /FlateDecode", zlib.compress(payload, 9)
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode("ascii") + objects[number] + b"\nendobj\n"
    xref_offset = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for number in range(1, size):
        offset = offsets.get(number)
        if offset is None:
            out += b"0000000000 65535 f \n"
        else:
            out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def _page_text(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line)


def _layout_text() -> str:
    return "\n".join(f"{left} {middle} {right}" for left, middle, right in LAYOUT_ROWS)


def build_all() -> dict[str, bytes]:
    return {
        "t060_plain.pdf": build_pdf(PLAIN_PAGES),
        "t060_layout.pdf": build_pdf([[]], layout=True),
        "t060_scanned.pdf": build_pdf([[]], scan_pages={1}),
        "t060_partial.pdf": build_pdf(PARTIAL_PAGES, scan_pages={2}),
        "t060_short.pdf": build_pdf([SHORT_PAGE]),
    }


def build_reference(payloads: dict[str, bytes]) -> dict:
    page_texts = {
        "t060_plain.pdf": PLAIN_REFERENCE,
        "t060_layout.pdf": [_layout_text()],
        "t060_scanned.pdf": [""],
        "t060_partial.pdf": [
            _page_text(PARTIAL_PAGES[0]),
            "",
            _page_text(PARTIAL_PAGES[2]),
        ],
        "t060_short.pdf": [_page_text(SHORT_PAGE)],
    }
    fixtures = {}
    for name, content in payloads.items():
        pages = page_texts[name]
        fixtures[name] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "media_type": PDF_MEDIA_TYPE,
            "page_count": len(pages),
            "pages": pages,
            "pages_without_text": [
                index + 1 for index, text in enumerate(pages) if not text
            ],
        }
    return {
        "generator": "tests/fixtures/f10/build_pdf_fixtures.py",
        "min_text_runes": MIN_TEXT_RUNES,
        "hand_checked": (
            "page texts were compared with a rendered page image during T060; "
            "the scanned fixture renders one image and no text"
        ),
        "fixtures": fixtures,
    }


if __name__ == "__main__":
    payloads = build_all()
    for name, content in payloads.items():
        path = HERE / name
        path.write_bytes(content)
        print(
            f"wrote {path} ({len(content)} bytes, "
            f"sha256 {hashlib.sha256(content).hexdigest()})"
        )
    reference = build_reference(payloads)
    REFERENCE_PATH.write_text(
        json.dumps(reference, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {REFERENCE_PATH}")
