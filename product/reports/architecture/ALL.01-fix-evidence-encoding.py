"""Repair ALL.01 evidence logs captured with mixed PowerShell encodings, then redact.

Some logs were started with ``Out-File -Encoding utf8`` (UTF-8 BOM) and then appended
with ``Tee-Object`` (UTF-16LE) on Windows PowerShell 5.1. A naive UTF-8 decode leaves
the UTF-16LE tail as NUL-interleaved text. This script detects that shape (a NUL byte
inside the file), decodes the UTF-8 head up to the first UTF-16 code unit and the
UTF-16LE tail separately, and rewrites the whole log as UTF-8 without BOM. It also
applies the same credential redaction as ``ALL.01-redact-evidence.py``.

Usage (from ``product``)::

    python -X utf8 reports/architecture/ALL.01-fix-evidence-encoding.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parent
OUTPUT_GLOB = "ALL.01-*.txt"

DSN = re.compile(r"postgresql://[^\s'\"<>]+")
SIGNING = re.compile(r"t024-signing-[A-Za-z0-9_-]+")
PASSWORD_ASSIGN = re.compile(
    r"(?i)(ORCHARA_PG_PASSWORD\s*[=:]\s*)['\"]?[^\s'\"]+['\"]?"
)


def redact(text: str) -> tuple[str, int]:
    total = 0
    for pattern, replacement in (
        (DSN, "postgresql://<REDACTED>"),
        (SIGNING, "<REDACTED-SIGNING-KEY>"),
        (PASSWORD_ASSIGN, r"\1<REDACTED>"),
    ):
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


def decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    nul = raw.find(b"\x00")
    if nul == -1:
        return raw.decode("utf-8", errors="replace")
    # The first UTF-16LE code unit is the byte pair (nul-1, 0x00) for the usual
    # ASCII first output character; walk back one byte when it is an ASCII byte.
    start = nul - 1 if nul > 0 and raw[nul - 1] < 0x80 else nul
    head = raw[:start].decode("utf-8", errors="replace")
    tail = raw[start:].decode("utf-16-le", errors="replace")
    return head + tail


def main() -> int:
    for path in sorted(EVIDENCE_DIR.glob(OUTPUT_GLOB)):
        raw = path.read_bytes()
        text = decode(raw)
        cleaned, count = redact(text)
        path.write_bytes(cleaned.encode("utf-8"))
        print(f"{path.name}: redactions={count} nul_after={path.read_bytes().count(0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
