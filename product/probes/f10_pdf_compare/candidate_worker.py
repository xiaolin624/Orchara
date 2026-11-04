"""F10/T060 comparison worker: extract + chunk one candidate over fixtures.

Run as a child process by ``run_comparison.py`` so every candidate is measured the
same way (process wall clock and peak RSS are sampled by the parent). Output is the
uniform JSON shape the harness consumes:

    {"candidate": "pymupdf", "version": "...", "fixtures": [
        {"file": ..., "sha256": ..., "compute_ns": int,
         "pages": ["..."], "chunks": [{"text","page","page_char_start","page_char_end"}]}
    ]}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import candidates


def package_version(backend: str) -> str:
    if backend == "pymupdf":
        import fitz

        return str(fitz.VersionBind)
    if backend == "pypdf":
        import pypdf

        return str(pypdf.__version__)
    if backend == "pdfminer":
        import pdfminer

        return str(getattr(pdfminer, "__version__", "unknown"))
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="F10/T060 comparison candidate worker")
    parser.add_argument("--candidate", required=True, choices=sorted(candidates.EXTRACTORS))
    parser.add_argument("--out", required=True)
    parser.add_argument("fixtures", nargs="+")
    args = parser.parse_args()

    records = []
    fixture_paths = [Path(path) for path in args.fixtures]
    # Warm the extractor once (font caches, lazy native modules) so the first
    # measured fixture is not charged for interpreter/extension startup. The Go
    # baseline has no analogue because it is a fresh process either way; the
    # process wall clock is reported separately and includes startup.
    if fixture_paths:
        candidates.extract_pages(str(fixture_paths[0]), args.candidate)
    for path in fixture_paths:
        raw = path.read_bytes()
        started = time.perf_counter_ns()
        pages = candidates.extract_pages(str(path), args.candidate)
        chunks = candidates.sentence_chunks(pages)
        compute_ns = time.perf_counter_ns() - started
        records.append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "compute_ns": compute_ns,
                "pages": pages,
                "chunks": chunks,
            }
        )
    payload = {
        "candidate": args.candidate,
        "version": package_version(args.candidate),
        "fixtures": records,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
