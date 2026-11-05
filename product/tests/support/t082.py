"""F08/T082 real-stack helpers: the bounded handover preview and the fixed-version switch.

Everything here drives the real platform: a throwaway PostgreSQL 16 database
migrated by the compiled ``cmd/admin`` binary, the compiled ``cmd/api`` behind real
cookie+CSRF HTTP and the real filesystem object store. A handover preview is a
server-produced object read over real HTTP; nothing in this module recomputes the
summary text, so an assertion about the preview can never be true by construction.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from support import f00b, f08
from support import stack as st

PREVIEW_SUFFIX = "/handoff-preview"

PREVIEW_FIELDS = {
    "conversation_id",
    "from_version_id",
    "source_run_ids",
    "artifact_ids",
    "summary",
    "summary_sha256",
    "source_runs",
    "artifacts",
    "limits",
}
PREVIEW_SOURCE_RUN_FIELDS = {
    "run_id",
    "application_id",
    "application_name",
    "version_id",
    "version_no",
    "created_at",
}
PREVIEW_ARTIFACT_FIELDS = {
    "artifact_id",
    "purpose",
    "media_type",
    "size_bytes",
    "sha256",
}
PREVIEW_LIMITS = {"max_source_runs": 5, "max_artifacts": 5, "max_summary_runes": 2000}

ERROR_HANDOFF_SCOPE_EXCEEDED = "handoff_scope_exceeded"
ERROR_HANDOFF_SOURCE_UNKNOWN = "handoff_source_unknown"
ERROR_HANDOFF_NOT_CONFIRMED = "handoff_not_confirmed"
ERROR_VALIDATION = "validation_failed"
ERROR_RESOURCE_NOT_FOUND = "resource_not_found"
ERROR_VERSION_REVOKED = "version_revoked"


# ---------------------------------------------------------------------------
# the handover preview endpoint (non-contract, documented in ADR 0030)
# ---------------------------------------------------------------------------
def preview_path(conversation_id: str) -> str:
    return f08.conversation_path(conversation_id) + PREVIEW_SUFFIX


def preview(
    session,
    conversation_id: str,
    source_run_ids,
    artifact_ids=None,
    *,
    body: dict | None = None,
    csrf: bool = True,
):
    payload = body if body is not None else {
        "source_run_ids": list(source_run_ids or []),
        "artifact_ids": list(artifact_ids or []),
    }
    return session.client.post(
        preview_path(conversation_id),
        body=payload,
        csrf=session.csrf if csrf else None,
    )


def preview_of(response) -> dict:
    """Assert the documented preview shape and return it."""
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == PREVIEW_FIELDS, body
    uuid.UUID(body["conversation_id"])
    assert body["from_version_id"] is None or uuid.UUID(body["from_version_id"])
    assert body["source_run_ids"] == sorted(body["source_run_ids"]), body
    assert body["artifact_ids"] == sorted(body["artifact_ids"]), body
    assert isinstance(body["summary"], str) and body["summary"].strip(), body
    assert len(body["summary_sha256"]) == 64, body
    assert hashlib.sha256(body["summary"].encode("utf-8")).hexdigest() == body["summary_sha256"]
    assert body["limits"] == PREVIEW_LIMITS, body["limits"]
    for run in body["source_runs"]:
        assert set(run) == PREVIEW_SOURCE_RUN_FIELDS, run
        uuid.UUID(run["run_id"])
        uuid.UUID(run["application_id"])
        if run["version_id"] is not None:
            uuid.UUID(run["version_id"])
        assert isinstance(run["version_no"], int)
        assert run["application_name"]
        assert run["created_at"]
    for artifact in body["artifacts"]:
        assert set(artifact) == PREVIEW_ARTIFACT_FIELDS, artifact
        uuid.UUID(artifact["artifact_id"])
        assert len(artifact["sha256"]) == 64, artifact
        assert artifact["size_bytes"] >= 0, artifact
    return body


def handoff_from(preview_body: dict) -> dict:
    """The exact RequestInput.handoff object the page builds from a preview.

    The summary is copied from the server response; a test never writes one itself,
    which is what makes "the new application only receives the previewed minimum"
    observable rather than tautological.
    """
    return {
        "source_run_ids": list(preview_body["source_run_ids"]),
        "artifact_ids": list(preview_body["artifact_ids"]),
        "summary": preview_body["summary"],
        "confirmed": True,
    }


# ---------------------------------------------------------------------------
# fixed-version switching
# ---------------------------------------------------------------------------
def run_cancel(session, run_id: str):
    return session.client.post(f"/api/v1/runs/{run_id}/cancel", csrf=session.csrf)


def run_row(dbname: str, run_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, request_id::text AS request_id, "
        "conversation_id::text AS conversation_id, owner_id::text AS owner_id, "
        "kind, state, revision, application_version_id::text AS application_version_id, "
        "parent_run_id::text AS parent_run_id, input_artifact_id::text AS input_artifact_id, "
        "output_artifact_id::text AS output_artifact_id "
        "FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    return rows[0] if rows else None


def route_jobs(dbname: str, request_id: str) -> list[dict]:
    return f08.background_jobs(dbname, target_id=request_id, kind="route_request")


def routing_candidate_rows(dbname: str, request_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT request_id::text AS request_id, application_version_id::text AS application_version_id, "
        "rank FROM platform.request_routing_candidates "
        f"WHERE request_id = {st.quote_literal(request_id)}::uuid ORDER BY rank",
    )


def routing_decision_rows(dbname: str, request_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT request_id::text AS request_id, attempt, decision, "
        "selected_version_id::text AS selected_version_id "
        "FROM platform.request_routing_decisions "
        f"WHERE request_id = {st.quote_literal(request_id)}::uuid ORDER BY attempt",
    )


def model_call_count(dbname: str, request_id: str) -> int:
    """Every control-model call attributed to this request, by either ledger key."""
    value = st.sql(
        dbname,
        "SELECT count(*)::text FROM platform.platform_model_calls AS c "
        "WHERE c.request_id = " + st.quote_literal(request_id) + "::uuid "
        "   OR c.background_job_id IN (SELECT id FROM platform.background_jobs "
        "        WHERE target_id = " + st.quote_literal(request_id) + "::uuid)",
    )
    return int(value)


def assert_no_routing(dbname: str, request_id: str) -> None:
    """A fixed-version dispatch creates no routing intention and no control call."""
    assert route_jobs(dbname, request_id) == [], request_id
    assert routing_candidate_rows(dbname, request_id) == [], request_id
    assert routing_decision_rows(dbname, request_id) == [], request_id
    assert model_call_count(dbname, request_id) == 0, request_id


# ---------------------------------------------------------------------------
# handover scope rows
# ---------------------------------------------------------------------------
def run_relation_ids(dbname: str, run_ids, *, relations: tuple[str, ...] | None = None) -> set[str]:
    if not run_ids:
        return set()
    literal = "ARRAY[" + ",".join(st.quote_literal(value) + "::uuid" for value in run_ids) + "]"
    clause = ""
    if relations is not None:
        clause = " AND relation IN (" + ",".join(
            st.quote_literal(value) for value in relations) + ")"
    rows = st.sql_rows(
        dbname,
        "SELECT artifact_id::text AS artifact_id, relation FROM platform.run_artifacts "
        f"WHERE run_id = ANY({literal})" + clause,
    )
    return {row["artifact_id"] for row in rows}


def memory_texts_of_application(session, stack, application_id: str) -> list[str]:
    """The user-visible memory read for exactly one application (list + body)."""
    from support import t054, t058

    listed = t054.list_memories(session, application_id=application_id)
    assert listed.status == 200, listed.text
    return [
        t058.text_of_memory(stack, t058.row_of_memory(stack.dbname, item["id"]))
        for item in listed.json["items"]
    ]


@dataclass
class SwitchFixture:
    """One conversation with two published applications and a source Run."""

    conversation_id: str
    version_a: object
    version_b: object
    request_a: dict
    run_a: str


def assert_single_live_top_run(stack, conversation_id: str, *, expected_run_id: str) -> None:
    live = f08.live_top_runs(stack.dbname, conversation_id)
    assert len(live) == 1, live
    assert live[0]["id"] == expected_run_id, live
