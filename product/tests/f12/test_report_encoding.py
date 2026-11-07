"""Report-encoding guard: no BOM, no replacement chars, no private-use mojibake.

The T098 report was committed after a UTF-8 -> GBK decode round-trip: it started
with a BOM and contained private-use-area characters that cannot be mapped back
to the original text (the review proved gbk/gb18030/cp1252/latin-1 are all
irreversible). This test keeps that class of corruption out of every committed
report, not only the T098 file.
"""

from __future__ import annotations

from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
# The F12 batch owns reports/f12; other tickets' reports are scanned by hand and
# reported in REVIEW-FIXES.md, never edited from this ticket (merge protocol).
REPORTS_ROOT = PRODUCT_ROOT / "reports" / "f12"

CORRUPTION_MARKERS = {
    "utf-8-replacement-char": "\ufffd",
    "bom-mid-file": "\ufeff",
}
PUA_START = 0xE000
PUA_END = 0xF8FF


def _corruption(path: Path) -> list[str]:
    raw = path.read_bytes()
    problems: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("bom")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return problems + [f"not-utf8: {exc}"]
    for name, marker in CORRUPTION_MARKERS.items():
        if name == "bom-mid-file" and text.startswith(marker):
            continue
        if marker in text:
            problems.append(name)
    pua = [character for character in text if PUA_START <= ord(character) <= PUA_END]
    if pua:
        problems.append(f"private-use-area:{len(pua)}")
    return problems


def test_committed_reports_are_clean_utf8_without_bom_or_mojibake() -> None:
    files = sorted(
        path
        for path in REPORTS_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".json"}
    )
    assert files, "no committed report files were found"
    offenders = {
        str(path.relative_to(PRODUCT_ROOT)): _corruption(path)
        for path in files
    }
    offenders = {path: problems for path, problems in offenders.items() if problems}
    assert offenders == {}, f"corrupted report encodings: {offenders}"
