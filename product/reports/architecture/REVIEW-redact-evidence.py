"""Redact local test credentials from the REVIEW-2 evidence logs.

The real-stack pytest failures print a ``Stack`` repr containing the local
development PostgreSQL DSN and a one-off ``t024-signing-*`` key. Those values are
never copied into reports. This script replaces them with the same placeholders
used by ``SECURITY-redaction-note.md`` and normalizes the logs to UTF-8 (no BOM).

Usage (from ``product``)::

    python -X utf8 reports/architecture/REVIEW-redact-evidence.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parent
OUTPUT_GLOB = "REVIEW-*.txt"

# Generic patterns only: never embed a concrete credential here.
DSN = re.compile(r"postgresql://[^\s'\"<>]+")
SIGNING = re.compile(r"t024-signing-[A-Za-z0-9_-]+")
PASSWORD_ASSIGN = re.compile(
    r"(?i)(ORCHARA_PG_PASSWORD\s*[=:]\s*)['\"]?[^\s'\"]+['\"]?"
)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8")


def redact(text: str) -> tuple[str, int]:
    count = 0
    for pattern, replacement in (
        (DSN, "postgresql://<REDACTED>"),
        (SIGNING, "<REDACTED-SIGNING-KEY>"),
        (PASSWORD_ASSIGN, r"\1<REDACTED>"),
    ):
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count


def main() -> int:
    changed = 0
    total = 0
    for path in sorted(EVIDENCE_DIR.glob(OUTPUT_GLOB)):
        text = read_text(path)
        cleaned, count = redact(text)
        path.write_bytes(cleaned.encode("utf-8"))
        changed += 1
        total += count
        print(f"{path.name}: redactions={count}")
    print(f"files={changed} redactions={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
