"""F10/T064 real-stack helpers: the knowledge answer surface.

The answer endpoint is the documented non-contract surface of ADR 0012 (T064):
one owner-scoped question over one knowledge base, answered by the labelled
extractive stub, with every citation validated against *this* retrieval and
re-read under the current ACL/revision before the answer is returned.

Everything here runs against the real stack: the previous ticket's PostgreSQL
database, the compiled ``cmd/api`` binary, real HTTP and the same sealed object
bytes. The fault server is a second real API process with a documented dev-stub
answer-generator fault in its deployment environment; it is not a mock of the
endpoint or of the validator.
"""

from __future__ import annotations

import json
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from support import f00b, t024, t055
from support import stack as st

ANSWER_FAULTS_ENV = "ORCHARA_KNOWLEDGE_ANSWER_FAULTS"
ANSWER_GENERATOR_KIND = "extractive_stub"
ANSWER_GENERATOR_VERSION = "extractive-stub/1"
ANSWER_MODEL_PROVIDER = "not_configured"

OUTCOME_ANSWERED = "answered"
OUTCOME_NO_RESULTS = "no_results"
OUTCOME_WITHOUT_CITATION = "answer_without_citation"
OUTCOME_CITATION_NOT_FOUND = "citation_not_found"
OUTCOME_CITATION_UNREADABLE = "citation_unreadable"

# The fabricated id the `foreign-chunk` fault cites (mirrors the Go constant).
FAULT_FOREIGN_CHUNK_ID = "00000000-0000-4000-8000-0000000000ff"

# One answer may cite at most three retrieved chunks (mirrors AnswerCiteLimit).
ANSWER_CITE_LIMIT = 3


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def answer_path(base_id: str) -> str:
    return "/api/v1/knowledge-bases/" + urllib.parse.quote(base_id, safe="") + "/answer"


def answer(
    session: f00b.Session,
    base_id: str,
    query: str,
    *,
    top_k: int | None = None,
    csrf: bool = True,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    client=None,
) -> st.Response:
    http = client or session.client
    if body is None:
        body = {"query": query}
        if top_k is not None:
            body["top_k"] = top_k
    return http.post(
        answer_path(base_id),
        body=body,
        csrf=session.csrf if csrf else None,
        headers=headers,
    )


def citations_of(payload: dict) -> list[dict]:
    return list((payload.get("answer") or {}).get("citations") or [])


def retrieval_chunk_ids(payload: dict) -> list[str]:
    return list((payload.get("retrieval") or {}).get("chunk_ids") or [])


# ---------------------------------------------------------------------------
# sessions / the second faulted API process
# ---------------------------------------------------------------------------
@dataclass
class _ServerSessionSource:
    """The two fields ``f00b.session_for`` needs, for a non-stack API server."""

    server: st.ApiServer

    def client(self) -> st.HttpClient:
        return st.HttpClient(self.server.base_url)


def login_on(server: st.ApiServer, login: str, password: str) -> f00b.Session:
    """Sign in against a specific real API process (same database)."""
    return f00b.session_for(_ServerSessionSource(server), login, password)


@contextmanager
def faulted_answer_server(api_server_factory, stack, directory: Path, faults: str):
    """Start one extra real API process with the documented answer-stub fault.

    Same database and object directory as the primary server; its own HTTP port,
    internal certificate and signing key from the stack. ``faults`` is the exact
    deployment value of ``ORCHARA_KNOWLEDGE_ANSWER_FAULTS``.
    """
    server, _certificates = t024.start_second_api(
        api_server_factory,
        stack,
        directory,
        signing_key=stack.signing_key,
        extra={ANSWER_FAULTS_ENV: faults},
    )
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# database read models (test-side truth, never trusted back from the API)
# ---------------------------------------------------------------------------
def chunk_row(dbname: str, chunk_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, document_id::text AS document_id, document_revision, "
        "chunk_no, body_artifact_id::text AS body_artifact_id, citation::text AS citation, "
        "embedding_model_revision, coalesce(array_length(embedding, 1), 0) AS embedding_length "
        "FROM platform.knowledge_chunks "
        f"WHERE id = {st.quote_literal(chunk_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def insert_forged_chunk(
    dbname: str,
    *,
    chunk_id: str,
    document_id: str,
    document_revision: int,
    chunk_no: int,
    body_artifact_id: str,
    citation: dict,
    template_chunk_id: str,
) -> None:
    """Insert one chunk row that points at another principal's sealed body.

    This is the "unauthorized chunk" shape: the row is real, its body artifact is
    real and ready, but the artifact belongs to a different principal. Only the
    SQL ACL predicate can keep it out of retrieval and out of an answer.
    """
    st.sql(
        dbname,
        "INSERT INTO platform.knowledge_chunks "
        "(id, document_id, document_revision, chunk_no, body_artifact_id, citation, "
        " embedding_model_revision, embedding, embedding_dimension) "
        f"SELECT {st.quote_literal(chunk_id)}::uuid, {st.quote_literal(document_id)}::uuid, "
        f"{int(document_revision)}, {int(chunk_no)}, {st.quote_literal(body_artifact_id)}::uuid, "
        f"{st.quote_literal(json.dumps(citation))}::jsonb, embedding_model_revision, "
        "embedding, embedding_dimension "
        "FROM platform.knowledge_chunks "
        f"WHERE id = {st.quote_literal(template_chunk_id)}::uuid",
    )


def revoke_document(dbname: str, document_id: str) -> None:
    """Write the F07-shaped revocation tombstone (delete governance owns the job)."""
    st.sql(
        dbname,
        "UPDATE platform.knowledge_documents SET state = 'deleted', deleted_at = now() "
        f"WHERE id = {st.quote_literal(document_id)}::uuid",
    )


def knowledge_base_revision(dbname: str, base_id: str) -> int:
    return int(t055.knowledge_base_row(dbname, base_id)["revision"])
