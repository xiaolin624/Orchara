"""F04B/T088 real-stack helpers: the retrieve node over the platform knowledge ACL.

A retrieve node names stable knowledge-base ids in its sealed config and binds a
string ``query`` input; at execution time the supervisor calls the non-contract
internal retrieval route, the API derives the run owner from the live attempt and
runs the F10 knowledge service's ACL-filtered Query per base. Everything here uses
the real stack: the compiled binaries, real PostgreSQL, real mTLS, real knowledge
ingestion (``support/t055``) and the real supervisor ``exec --once``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from support import f00b, t027, t033, t042, t055

# The documented non-contract node output (reports/f04b/T088-retrieve-node.md).
RETRIEVE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "document_revision": {"type": "integer"},
                    "document_state": {"type": "string"},
                    "chunk_no": {"type": "integer"},
                    "span": {
                        "type": "object",
                        "properties": {
                            "start_char": {"type": "integer"},
                            "end_char": {"type": "integer"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                        },
                        "required": ["start_char", "end_char", "start_line", "end_line"],
                        "additionalProperties": False,
                    },
                    "excerpt": {"type": "string"},
                    "citation": {"type": "object"},
                },
                "required": [
                    "chunk_id", "document_id", "document_revision", "document_state",
                    "chunk_no", "span", "excerpt", "citation",
                ],
                "additionalProperties": False,
            },
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "document_revision": {"type": "integer"},
                    "span": {"type": "object"},
                    "excerpt": {"type": "string"},
                },
                "required": ["chunk_id", "document_id", "document_revision", "span", "excerpt"],
                "additionalProperties": False,
            },
        },
        "count": {"type": "integer"},
    },
    "required": ["items", "citations", "count"],
    "additionalProperties": False,
}

ITEM_KEYS = {
    "chunk_id", "document_id", "document_revision", "document_state", "chunk_no",
    "span", "excerpt", "citation",
}
CITATION_KEYS = {"chunk_id", "document_id", "document_revision", "span", "excerpt"}
# The internal worker retrieval route (worker mTLS, non-contract) still returns the
# full text and the vector score so the server can rank/merge; those fields are not
# node data and never reach the run view/artifact consumer. Kept separate so the
# node-output narrowing stays pinned.
INTERNAL_ITEM_KEYS = {
    "chunk_id", "document_id", "document_revision", "document_state", "chunk_no",
    "span", "text", "score", "citation",
}


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------
def _input_schema(fields: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in fields},
        "required": list(fields),
        "additionalProperties": False,
    }


def _knowledge_base_ids(value) -> list[str]:
    """Accept one UUID or an iterable of UUIDs (a bare string is one id, not chars)."""
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def retrieve_node(
    node_id: str,
    knowledge_base_ids,
    *,
    top_k: int = 5,
    input_field: str = "question",
) -> dict:
    return {
        "id": node_id,
        "type": "retrieve",
        "inputs": {"query": {"ref": "input#/" + input_field}},
        "config": {
            "knowledge_base_ids": _knowledge_base_ids(knowledge_base_ids),
            "top_k": top_k,
        },
        "output_schema": dict(RETRIEVE_OUTPUT_SCHEMA),
    }


def single_retrieve_definition(knowledge_base_ids, *, top_k: int = 5) -> dict:
    """start -> search(retrieve) -> end(citations/count)."""
    end = t027.end_node()
    end["inputs"] = {
        "citations": {"ref": "node:search#/citations"},
        "count": {"ref": "node:search#/count"},
    }
    definition = t027.definition(
        [t027.start_node(), retrieve_node("search", knowledge_base_ids, top_k=top_k), end],
        [
            {"source": "start", "target": "search"},
            {"source": "search", "target": "end"},
        ],
    )
    definition["input_schema"] = _input_schema(("question",))
    return definition


def parallel_retrieve_definition(
    left_knowledge_base_ids, right_knowledge_base_ids, *, top_k: int = 2
) -> dict:
    """start forks onto two retrieve branches that converge at one join."""
    join = {
        "id": "join",
        "type": "join",
        "inputs": {},
        "config": {
            "fork_id": "start",
            "mode": "all_active",
            "required_sources": ["left_search", "right_search"],
            "optional_sources": [],
        },
        "output_schema": {"type": "object"},
    }
    end = t027.end_node()
    end["inputs"] = {"results": {"ref": "node:join#/results"}}
    definition = t027.definition(
        [
            t027.start_node(),
            retrieve_node(
                "left_search", left_knowledge_base_ids, top_k=top_k, input_field="left"
            ),
            retrieve_node(
                "right_search", right_knowledge_base_ids, top_k=top_k, input_field="right"
            ),
            join,
            end,
        ],
        [
            {"source": "start", "target": "left_search"},
            {"source": "start", "target": "right_search"},
            {"source": "left_search", "target": "join"},
            {"source": "right_search", "target": "join"},
            {"source": "join", "target": "end"},
        ],
    )
    definition["input_schema"] = _input_schema(("left", "right"))
    return definition


# ---------------------------------------------------------------------------
# knowledge fixtures (real ingestion through t055)
# ---------------------------------------------------------------------------
def create_knowledge_base(session, name: str) -> str:
    response = t055.create_knowledge_base(session, name)
    assert response.status == 201, response.text
    return response.json["id"]


def ingest_ready_document(
    stack,
    session,
    base_id: str,
    content,
    *,
    log_name: str = "maintenance-t088.log",
) -> tuple[str, str]:
    """Upload + finalize one source, ingest it through the real pipeline, return (document, artifact)."""
    artifact_id = t055.prepare_source(stack, session, content, media_type=t055.TEXT)
    with t055.ingest_pools(stack, stack.server.log_path.parent / log_name):
        accepted = t055.ingest_document(session, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(session, document_id, {"ready", "failed"})
        assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    return document_id, artifact_id


def ready_knowledge_base(
    stack,
    session,
    name: str,
    content,
    *,
    log_name: str = "maintenance-t088.log",
) -> tuple[str, str]:
    base_id = create_knowledge_base(session, name)
    document_id, _artifact_id = ingest_ready_document(
        stack, session, base_id, content, log_name=log_name
    )
    return base_id, document_id


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Owner:
    """One real creator plus the application whose draft carries the definition."""

    creator: f00b.Session
    application_id: str


def submit_run(stack, creator, definition: dict, *, input_value: dict, name: str):
    """Create one owner application with the definition and queue one debug run.

    The run owner is the given session's principal: the knowledge bases the test
    creates with the same session are the ones this run may retrieve from.
    """
    application_id = t027.application_with_definition(creator, definition, name=name)
    owner = Owner(creator=creator, application_id=application_id)
    run = t042.submit_linear_run(owner, application_id, input_value=input_value)
    return owner, application_id, run


def run_once(stack):
    return t042.run_linear_once(stack)


def node_states(dbname: str, run_id: str) -> dict[str, list[str]]:
    from support import t083

    return t083.node_states(dbname, run_id)


def final_output_document(session, dbname: str, run_id: str) -> dict:
    artifact_id = t033.output_artifact_id(dbname, run_id)
    assert artifact_id, "the run sealed no final output"
    content = t033.artifact_content(session, artifact_id)
    assert content.status == 200, content.text
    return json.loads(content.raw)


# ---------------------------------------------------------------------------
# the internal retrieval route (direct mTLS call for the ACL counters)
# ---------------------------------------------------------------------------
class InternalRetrievalClient(f00b.InternalClient):
    """The non-contract /internal/v1/runs/{run_id}/retrieval route."""

    def retrieve(self, run_id: str, body: dict, **kwargs):
        return self.post(f"/internal/v1/runs/{run_id}/retrieval", body, **kwargs)


def retrieval_client(stack, role: str = "worker") -> InternalRetrievalClient:
    return InternalRetrievalClient(
        stack.certificates.internal_url,
        stack.certificates.ca.cert,
        stack.certificates.client(role),
    )


def claim_one_run(stack, *, execution_unit_id: str = "t088-unit") -> dict:
    """Claim the one queued debug run with a real supervisor certificate."""
    from support import t029

    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t088-supervisor",
        execution_unit_id=execution_unit_id,
    )
    assert response.status == 200, response.text
    return response.json


def chunk_body_text(stack, document_id: str, revision: int, chunk_no: int) -> str:
    """Read one sealed chunk body's bytes (the test's independent truth)."""
    chunks = t055.chunk_rows(stack.dbname, document_id, revision)
    match = [chunk for chunk in chunks if int(chunk["chunk_no"]) == chunk_no]
    assert len(match) == 1, (document_id, chunk_no, chunks)
    artifact = t055.artifact_row(stack.dbname, match[0]["body_artifact_id"])
    return t055.object_text(stack.object_dir, artifact)


def chunk_row(stack, document_id: str, revision: int, chunk_no: int) -> dict:
    chunks = t055.chunk_rows(stack.dbname, document_id, revision)
    match = [chunk for chunk in chunks if int(chunk["chunk_no"]) == chunk_no]
    assert len(match) == 1, (document_id, chunk_no, chunks)
    return match[0]
