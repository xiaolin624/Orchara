"""F10/T060 comparison candidates.

The baseline is the platform's registered Go parser/chunker (run through the
``go_baseline`` probe; not Python). The candidates below are *available
substitutes* for the Docling Native + Docling chunker combination the ticket asks
about: Docling is not installed and its installation attempt is recorded
separately. None of these is labelled Docling.

Candidate B (the ticket's requested second runnable implementation) is PyMuPDF
extraction + the deterministic paragraph/sentence chunker in this module. pypdf
and pdfminer.six share the same chunker, so the extraction quality of three
libraries is compared while the chunking rule is held constant.
"""

from __future__ import annotations

import re

MAX_CHUNK_CHARS = 1200
OVERLAP_CHARS = 200

CANDIDATES = {
    "pymupdf": {
        "label": "candidate-b-pymupdf-sentence",
        "kind": "available-substitute",
        "docling": False,
        "extractor": "PyMuPDF (fitz) page.get_text('text')",
        "chunker": "sentence-packer/1 (this probe, written for T060)",
        "licence": "AGPL-3.0",
        "note": (
            "Available substitute for Docling, NOT Docling: PyMuPDF extraction "
            "plus a sentence/paragraph packer written for this comparison."
        ),
    },
    "pypdf": {
        "label": "candidate-c-pypdf-sentence",
        "kind": "available-substitute",
        "docling": False,
        "extractor": "pypdf PdfReader.pages[i].extract_text()",
        "chunker": "sentence-packer/1 (this probe, written for T060)",
        "licence": "BSD-3-Clause",
        "note": "Available substitute for Docling, NOT Docling; extraction-only comparison.",
    },
    "pdfminer": {
        "label": "candidate-d-pdfminer-sentence",
        "kind": "available-substitute",
        "docling": False,
        "extractor": "pdfminer.six high_level.extract_text(page_numbers=[i])",
        "chunker": "sentence-packer/1 (this probe, written for T060)",
        "licence": "MIT",
        "note": "Available substitute for Docling, NOT Docling; extraction-only comparison.",
    },
}


def _normalized_page(text: str) -> str:
    """The candidate's page text: lines kept, trailing blank lines dropped."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def _extract_pymupdf(path: str) -> list[str]:
    import fitz  # PyMuPDF

    with fitz.open(path) as document:
        return [_normalized_page(page.get_text("text")) for page in document]


def _extract_pypdf(path: str) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    return [_normalized_page(page.extract_text() or "") for page in reader.pages]


def _extract_pdfminer(path: str) -> list[str]:
    from pdfminer.high_level import extract_text

    pages = []
    # extract_text returns the whole document when page_numbers spans all pages;
    # per-page calls keep the page identity exact for the page-attribution check.
    page_number = 0
    while True:
        try:
            text = extract_text(path, page_numbers=[page_number])
        except Exception:  # noqa: BLE001 - past the last page is an implementation detail
            break
        if text is None:
            break
        pages.append(_normalized_page(text))
        page_number += 1
        if page_number > 1000:
            break
    return pages


EXTRACTORS = {
    "pymupdf": _extract_pymupdf,
    "pypdf": _extract_pypdf,
    "pdfminer": _extract_pdfminer,
}


def extract_pages(path: str, backend: str) -> list[str]:
    return EXTRACTORS[backend](path)


def _split_units(canonical: str) -> list[str]:
    """Sentence units of one page; a sentence ends at . ! ? followed by a space."""
    units = []
    for paragraph in re.split(r"\s{2,}", canonical):
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph.strip()):
            sentence = sentence.strip()
            if sentence:
                units.append(sentence)
    return units


def sentence_chunks(
    pages: list[str],
    *,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> list[dict]:
    """Chunk each page into sentence-packed windows; chunks never cross a page.

    The page text is whitespace-collapsed first, so a chunk's offsets index the
    candidate's own page text (`page_char_start`/`page_char_end`). An oversized
    sentence is hard-split with the documented overlap.
    """
    chunks: list[dict] = []
    for page_number, page_text in enumerate(pages, start=1):
        canonical = " ".join(str(page_text or "").split())
        if not canonical:
            continue
        units = _split_units(canonical)
        cursor = 0
        pending: list[tuple[int, int, str]] = []
        pending_size = 0

        def flush() -> None:
            nonlocal pending, pending_size
            if not pending:
                return
            start = pending[0][0]
            end = pending[-1][1]
            chunks.append(
                {
                    "text": canonical[start:end],
                    "page": page_number,
                    "page_char_start": start,
                    "page_char_end": end,
                }
            )
            pending = []
            pending_size = 0

        for unit in units:
            start = canonical.find(unit, cursor)
            if start < 0:
                start = cursor
                end = min(len(canonical), start + len(unit))
            else:
                end = start + len(unit)
            cursor = end
            if len(unit) > max_chars:
                flush()
                window_start = start
                while window_start < end:
                    window_end = min(end, window_start + max_chars)
                    chunks.append(
                        {
                            "text": canonical[window_start:window_end],
                            "page": page_number,
                            "page_char_start": window_start,
                            "page_char_end": window_end,
                        }
                    )
                    if window_end >= end:
                        break
                    next_start = window_end - overlap
                    while next_start < window_end and next_start > window_start and not canonical[next_start - 1].isspace():
                        next_start += 1
                    window_start = next_start if next_start > window_start else window_end
                continue
            if pending and pending_size + 1 + len(unit) > max_chars:
                flush()
            if pending:
                pending_size += 1 + len(unit)
            else:
                pending_size = len(unit)
            pending.append((start, end, unit))
        flush()
    return chunks
