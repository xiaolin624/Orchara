"""F10/T060 real-stack helpers: plain-text PDF ingestion with page citations.

The stack is the T055 one: a throwaway PostgreSQL database migrated by the real
``cmd/admin migrate`` chain, the compiled ``cmd/api`` with a real filesystem
object store and signed links, and a real ``cmd/maintenance`` process running the
real ``document_ingest`` and ``artifact_finalize`` handlers.

Nothing here mocks the pipeline under test. The committed PDF fixtures and their
hand-checked page texts are frozen inputs; the independent truths below are read
from the sealed source bytes with a library other than the platform's parser
(PyMuPDF for the comparison harness) or from the committed reference, never from
the platform's own answer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from support import t055, t059

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "f10"

PDF = "application/pdf"
PURPOSE_KNOWLEDGE = "knowledge"
PARSER = "orchara-pdf-parser/1"
CHUNKER = "orchara-pdf-page-paragraph-chunker/1"
TEXT_RENDER = "pdf_pages/1"
MIN_TEXT_RUNES = 32
EMBEDDING_REVISION = "local-hash-v1"
DIMENSION = 384


# ---------------------------------------------------------------------------
# committed fixtures and the hand-checked reference
# ---------------------------------------------------------------------------
def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def reference() -> dict:
    return json.loads((FIXTURES / "t060_reference.json").read_text(encoding="utf-8"))


def reference_pages(name: str) -> list[str]:
    return list(reference()["fixtures"][name]["pages"])


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def non_whitespace_runes(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


# ---------------------------------------------------------------------------
# one upload through the real admission path
# ---------------------------------------------------------------------------
def upload_source(
    stack,
    session,
    content: bytes,
    *,
    seed_ready: bool = False,
    media_type: str = PDF,
) -> str:
    """Upload+finalize a source artifact, or seed a ready one.

    Seeding is used only for bytes the T028 admission gate would quarantine
    (an encrypted-looking or structurally broken PDF): those bytes must reach the
    *parser* to prove the parser's own refusal. The row and the sealed bytes are
    real in both paths.
    """
    if seed_ready:
        return t055.seed_ready_source_artifact(
            stack, session.principal_id, content, media_type=media_type
        )
    return t055.prepare_source(stack, session, content, media_type=media_type)


def encrypted_pdf() -> bytes:
    """A valid PDF whose trailer declares /Encrypt (no decryption is attempted)."""
    plain = fixture("t060_plain.pdf")
    assert b"/Root 1 0 R" in plain
    return plain.replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 99 0 R", 1)


def invalid_pdf() -> bytes:
    """Bytes that claim the PDF header but have no readable cross-reference."""
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n" + b"x" * 64


# ---------------------------------------------------------------------------
# reads used by the tests (the same helpers the earlier F10 tickets use)
# ---------------------------------------------------------------------------
admission_row = t059.admission_row
ingest_and_wait = t059.ingest_and_wait
ingest_many = t059.ingest_many
sealed_source_bytes = t059.sealed_source_bytes
chunk_body_text = t059.chunk_body_text
chunk_citations = t059.chunk_citations
assert_failed_document = t059.assert_failed_document
