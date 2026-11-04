"""F10/T060 comparison metrics.

Deliberately small, deterministic and independent of the platform: the reference
is the committed hand-checked page text, the extracted text comes from a
candidate, and every number is reproducible from those two inputs.

Definitions (all reported in the committed comparison JSON):

* ``word_coverage``  - longest-common-subsequence recall of reference word tokens
  ``[a-z0-9]+`` against the candidate's tokens. Order-aware, 0..1.
* ``char_ratio``     - difflib.SequenceMatcher ratio of whitespace-collapsed text.
* ``page_exact``     - a page whose whitespace-collapsed text equals the reference
  page exactly (layout/order preserved, not just the words present).
* ``page_attribution`` - fraction of chunks whose own tokens overlap their claimed
  page's reference tokens at least as much as any other page's. The page-bounded
  chunkers always claim exactly one page.
"""

from __future__ import annotations

import difflib
import re
import statistics

WORD_RE = re.compile(r"[a-z0-9]+")


def collapsed(text: str) -> str:
    return " ".join(str(text or "").split())


def tokens(text: str) -> list[str]:
    return WORD_RE.findall(collapsed(text).lower())


def _lcs_length(left: list[str], right: list[str]) -> int:
    if not left or not right:
        return 0
    if len(left) * len(right) > 4_000_000:
        # The fixtures are small; this guard keeps a pathological candidate from
        # turning the metric into an unbounded computation.
        return 0
    previous = [0] * (len(right) + 1)
    for item in left:
        current = [0]
        for index, other in enumerate(right, start=1):
            if item == other:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def word_coverage(reference_pages: list[str], candidate_pages: list[str]) -> float:
    reference_tokens = tokens("\n".join(reference_pages))
    candidate_tokens = tokens("\n".join(candidate_pages))
    if not reference_tokens:
        return 1.0 if not candidate_tokens else 0.0
    return round(_lcs_length(reference_tokens, candidate_tokens) / len(reference_tokens), 4)


def char_ratio(reference_pages: list[str], candidate_pages: list[str]) -> float:
    reference = collapsed("\n".join(reference_pages))
    candidate = collapsed("\n".join(candidate_pages))
    if not reference and not candidate:
        return 1.0
    return round(difflib.SequenceMatcher(None, reference, candidate).ratio(), 4)


def page_exact_matches(reference_pages: list[str], candidate_pages: list[str]) -> list[bool]:
    matches = []
    for index, reference in enumerate(reference_pages):
        candidate = candidate_pages[index] if index < len(candidate_pages) else ""
        matches.append(collapsed(reference) == collapsed(candidate))
    return matches


def page_attribution(
    reference_pages: list[str], chunks: list[dict]
) -> dict:
    if not chunks:
        return {"correct": 0, "total": 0, "ratio": None}
    reference_tokens = [set(tokens(page)) for page in reference_pages]
    correct = 0
    for chunk in chunks:
        chunk_tokens = set(tokens(chunk.get("text", "")))
        claimed = int(chunk.get("page") or 0)
        overlaps = [
            len(chunk_tokens & page_tokens) if page_tokens else 0
            for page_tokens in reference_tokens
        ]
        if not overlaps:
            continue
        best = max(overlaps)
        if best == 0:
            continue
        claimed_index = claimed - 1
        if 0 <= claimed_index < len(overlaps) and overlaps[claimed_index] == best:
            correct += 1
    return {
        "correct": correct,
        "total": len(chunks),
        "ratio": round(correct / len(chunks), 4) if chunks else None,
    }


def chunk_stats(chunks: list[dict]) -> dict:
    sizes = sorted(len(chunk.get("text", "")) for chunk in chunks)
    if not sizes:
        return {"count": 0, "min": 0, "median": 0, "max": 0, "mean": 0}
    return {
        "count": len(sizes),
        "min": sizes[0],
        "median": statistics.median(sizes),
        "max": sizes[-1],
        "mean": round(sum(sizes) / len(sizes), 1),
    }


def measure(reference: dict, candidate: dict) -> dict:
    """All quality numbers for one candidate on one fixture."""
    reference_pages = list(reference["pages"])
    candidate_pages = list(candidate.get("pages") or [])
    if len(candidate_pages) < len(reference_pages):
        candidate_pages.extend([""] * (len(reference_pages) - len(candidate_pages)))
    chunks = list(candidate.get("chunks") or [])
    exact = page_exact_matches(reference_pages, candidate_pages)
    return {
        "word_coverage": word_coverage(reference_pages, candidate_pages),
        "char_ratio": char_ratio(reference_pages, candidate_pages),
        "page_exact": exact,
        "page_exact_count": sum(1 for match in exact if match),
        "page_attribution": page_attribution(reference_pages, chunks),
        "chunk_stats": chunk_stats(chunks),
        "pages_without_text": [
            index + 1 for index, text in enumerate(candidate_pages) if not collapsed(text)
        ],
    }
