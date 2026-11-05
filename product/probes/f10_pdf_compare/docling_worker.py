#!/usr/bin/env python3
"""F10/T060 candidate A: real Docling Native + Docling chunker run.

This script is executed with the *isolated* environment's interpreter (the venv
created by ``attempt_docling.py``), never with the platform Python:

    <isolated-python> probes/f10_pdf_compare/docling_worker.py --out docling_attempt.json

Outcomes, recorded honestly:
  * ``blocked``   - Docling cannot be imported (installation failed) or a
                    conversion failed. ``reason`` and the raw error are recorded;
                    no quality number is invented.
  * ``measured``  - the converter and the Docling ``HierarchicalChunker`` really
                    ran over the frozen fixtures. Page text is reconstructed from
                    each text item's provenance (``prov[0].page_no``) and each
                    chunk's page comes from its doc-item provenance, so the
                    comparison has real page attribution.

The script always exits 0 after writing the JSON so the comparison harness can
report a blocked candidate as a result rather than as a crash.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import traceback
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRODUCT_ROOT = HERE.parents[1]
FIXTURES_DIR = PRODUCT_ROOT / "tests" / "fixtures" / "f10"
REFERENCE_PATH = FIXTURES_DIR / "t060_reference.json"

sys.path.insert(0, str(HERE))
import metrics  # noqa: E402

FIXTURES = ["t060_plain.pdf", "t060_layout.pdf", "t060_scanned.pdf", "t060_partial.pdf", "t060_short.pdf"]


def package_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except Exception as exc:  # noqa: BLE001
        return f"unavailable: {exc}"


def measure_peak_rss_mb():
    """The worker's own peak working set, so the candidate has a real memory number."""
    try:
        import psutil

        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", None) or getattr(info, "rss", 0)
        return round(int(peak) / (1024 * 1024), 2) if peak else None
    except Exception:  # noqa: BLE001 - absence is reported, not invented
        return None


def blocked(reason: str, error: str) -> dict:
    return {
        "status": "blocked",
        "reason": reason,
        "error": error[-8000:],
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "peak_rss_mb": measure_peak_rss_mb(),
        "note": (
            "Docling Native + HierarchicalChunker could not be executed in this "
            "environment; no quality or resource number is claimed for it."
        ),
    }


def page_texts(document) -> list[str]:
    """Reconstruct per-page text from the document's text items and provenance.

    Docling's document-level export joins blocks; the per-page reconstruction keeps
    the page identity the citation needs, using the same items the converter
    produced. It is not a native page export (that is stated in the result).
    """
    pages = {number: [] for number in getattr(document, "pages", {}) or {}}
    for item in getattr(document, "texts", []) or []:
        provinces = getattr(item, "prov", None) or []
        if not provinces:
            continue
        page_number = int(getattr(provinces[0], "page_no", 0) or 0)
        if page_number <= 0:
            continue
        pages.setdefault(page_number, []).append(str(getattr(item, "text", "") or ""))
    result = []
    for number in sorted(pages):
        result.append("\n".join(part for part in pages[number] if part.strip()))
    return result


def chunk_records(chunker, document) -> list[dict]:
    records = []
    for chunk in chunker.chunk(document):
        page = 0
        for item in getattr(chunk.meta, "doc_items", []) or []:
            for province in getattr(item, "prov", []) or []:
                page = int(getattr(province, "page_no", 0) or 0)
                if page:
                    break
            if page:
                break
        records.append({"text": str(chunk.text), "page": page})
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="F10/T060 Docling candidate attempt")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    try:
        from docling.document_converter import DocumentConverter
        from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker
    except Exception as exc:  # noqa: BLE001 - the whole point is to record this
        payload = blocked("docling import failed", traceback.format_exc())
        payload["import_error"] = str(exc)
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"docling blocked: {exc}")
        return 0

    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["fixtures"]
    converter = DocumentConverter()
    chunker = HierarchicalChunker()
    measured: dict = {}
    try:
        for name in FIXTURES:
            path = FIXTURES_DIR / name
            started = time.perf_counter()
            result = converter.convert(str(path))
            document = result.document
            pages = page_texts(document)
            chunks = chunk_records(chunker, document)
            wall = time.perf_counter() - started
            reference_pages = reference[name]["pages"]
            measurement = metrics.measure(
                {"pages": reference_pages},
                {"pages": pages, "chunks": chunks},
            )
            measured[name] = {
                "sha256": reference[name]["sha256"],
                "wall_s": round(wall, 3),
                "docling_version": package_version("docling"),
                "page_count": len(reference_pages),
                "page_texts_available": True,
                "page_text_source": "TextItem provenance reconstruction (not a native page export)",
                "word_coverage": measurement["word_coverage"],
                "char_ratio": measurement["char_ratio"],
                "page_exact_count": measurement["page_exact_count"],
                "page_exact": measurement["page_exact"],
                "page_attribution": measurement["page_attribution"],
                "chunk_count": len(chunks),
                "chunk_chars": metrics.chunk_stats([{"text": chunk["text"]} for chunk in chunks]),
                "chunks": chunks,
                "pages": pages,
                "pages_without_text": measurement["pages_without_text"],
            }
    except Exception:  # noqa: BLE001 - a failed conversion is a blocked candidate
        payload = blocked("docling conversion failed", traceback.format_exc())
        payload["packages"] = {
            "docling": package_version("docling"),
            "docling-core": package_version("docling-core"),
            "torch": package_version("torch"),
        }
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("docling conversion failed; recorded blocked")
        return 0

    payload = {
        "status": "measured",
        "reason": "",
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "packages": {
            "docling": package_version("docling"),
            "docling-core": package_version("docling-core"),
            "docling-parse": package_version("docling-parse"),
            "torch": package_version("torch"),
        },
        "pipeline": "DocumentConverter (Docling Native) + HierarchicalChunker",
        "fixtures": measured,
        "peak_rss_mb": measure_peak_rss_mb(),
        "note": (
            "Docling ran in its own isolated environment. Page text is "
            "reconstructed from text-item provenance; the document-level export "
            "itself does not expose pages."
        ),
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("docling measured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
