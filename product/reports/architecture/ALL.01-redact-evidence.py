"""Normalize the ALL.01 evidence logs to UTF-8 (no BOM) and redact local test credentials.

The C4 focused run printed a real-stack ``Stack`` repr in the pytest failure, which
contains the local development PostgreSQL DSN and a one-off ``t024-signing-*`` key.
Those values are never copied into reports; this script replaces them with the same
placeholders used by ``SECURITY-redaction-note.md`` before the evidence is committed.

Usage (from ``product``)::

    python -X utf8 reports/architecture/ALL.01-redact-evidence.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parent
OUTPUT_GLOB = "ALL.01-*.txt"

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
    for path in sorted(EVIDENCE_DIR.glob(OUTPUT_GLOB)):
        text = read_text(path)
        cleaned, count = redact(text)
        # Always rewrite: normalize UTF-16/BOM logs to UTF-8 without BOM.
        path.write_bytes(cleaned.encode("utf-8"))
        changed += 1
        print(f"{path.name}: redactions={count}")
    print(f"files={changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
