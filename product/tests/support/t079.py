"""F08/T079 real-stack helpers: routing, control calls, interactions and replies.

Everything here drives the real platform: the throwaway PostgreSQL 16 database
migrated by the compiled ``cmd/admin`` binary, the compiled ``cmd/api`` behind real
cookie+CSRF HTTP, the real filesystem object store, the compiled maintenance
process running the real ``route_request`` handler, and a real registered
``model_profile_versions`` row for the routing control model (the dev stub, labelled
``stub: true``).

Published versions are real compiler output (``t077.publish_version``). The only
hand-written rows are the deliberately seeded control-call ledger rows used to
prove the three-call budget; every routing outcome is read back from the
authoritative request/interaction/run/candidate/decision tables.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

from support import f00b, f08, t024, t077
from support import stack as st

REQUESTS = "/api/v1/requests"
ROUTE_MODEL_ID = "stub-route-control"
ROUTE_CONTROL_BUDGET = 3

REQUEST_FIELDS = f08.REQUEST_FIELDS


# ---------------------------------------------------------------------------
# the routing control model (a real registered candidate, marked in config)
# ---------------------------------------------------------------------------
def seed_route_control_model(stack, owner_id: str, *, model_id: str = ROUTE_MODEL_ID) -> dict:
    """Register the deployment's routing control model.

    It is a real ``model_profile_versions`` row (provider stub, passed validation,
    a real sealed manifest artifact) whose config declares ``route_control: true``.
    The server resolves it per call; no env var or handler input names a model.
    """
    return t024.seed_candidate(
        stack.dbname,
        stack.object_dir,
        owner_id,
        provider="stub",
        model_id=model_id,
        validation_state="passed",
        config={"route_control": True, "context_tokens": 8192},
    )


def seed_route_model_calls(stack, request_id: str, count: int, model_id: str) -> list[str]:
    """Seed `count` real platform_model_calls rows for the request's route job.

    The rows are the exact ledger shape (the job owns the row; XOR leaves
    request_id NULL); this is how the budget-exhaustion test reaches the cap
    deterministically without sleeping or racing a fourth model call.
    """
    jobs = f08.background_jobs(stack.dbname, target_id=request_id, kind="route_request")
    assert len(jobs) == 1, jobs
    job_id = jobs[0]["id"]
    calls = []
    for attempt in range(1, count + 1):
        call_id = f00b.new_uuid()
        st.sql(
            stack.dbname,
            "INSERT INTO platform.platform_model_calls "
            "(id, owner_id, background_job_id, model_profile_version_id, scope_key, "
            " logical_key, physical_attempt, state, currency, reserved_amount, params_sha256) VALUES ("
            f"{st.quote_literal(call_id)}::uuid, "
            f"(SELECT owner_id FROM platform.background_jobs WHERE id = {st.quote_literal(job_id)}::uuid), "
            f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(model_id)}::uuid, "
            f"{st.quote_literal(job_id)}, 'route:{request_id}:seeded', {attempt}, 'succeeded', "
            "'USD', 0, "
            f"{st.quote_literal('a' * 64)})",
        )
        calls.append(call_id)
    return calls


# ---------------------------------------------------------------------------
# real published versions with a chosen capability card
# ---------------------------------------------------------------------------
def publish(stack, session, *, purpose: str, required_inputs: list[str] | None = None,
            name: str = "t079 router application", examples: list[str] | None = None):
    return t077.publish_version(
        stack,
        session,
        purpose=purpose,
        required_inputs=required_inputs,
        name=name,
        examples=examples,
    )


def publish_offtopic(stack, session, *, name: str = "t079 off-topic application"):
    """A published, readable version whose card shares no token with the messages.

    The compiler requires all six card fields, so the fixture deliberately avoids
    the words the routing messages use (``sales``/``report``/``quarterly``); a
    default card's ``examples`` would otherwise make it a legitimate candidate.
    """
    return t077.publish_version(
        stack,
        session,
        purpose="invoice reconciliation",
        name=name,
        includes=["invoicing"],
        excludes=["external writes"],
        required_inputs=["invoice csv"],
        expected_outputs=["invoice"],
        examples=["invoice export", "ledger sync"],
    )


# ---------------------------------------------------------------------------
# request/reply endpoints
# ---------------------------------------------------------------------------
def reply_path(request_id: str) -> str:
    return REQUESTS + "/" + quote(str(request_id), safe="") + "/reply"


def submit(session, conversation_id: str, message: str, *, artifact_ids=None, key=None):
    return f08.submit_request(
        session, conversation_id, message,
        artifact_ids=list(artifact_ids or []), key=key,
    )


def reply(session, request_id: str, body: dict, *, key: str | None = None):
    return session.client.post(
        reply_path(request_id), body=body, csrf=session.csrf,
        headers={"Idempotency-Key": key or f00b.new_uuid()},
    )


def reply_accepted(response) -> dict:
    assert response.status == 202, response.text
    body = response.json
    assert set(body) == REQUEST_FIELDS, body
    return body


# ---------------------------------------------------------------------------
# the real route_request handler
# ---------------------------------------------------------------------------
@dataclass
class RouteRun:
    completed: object
    before_jobs: list[dict]

    @property
    def stdout(self) -> str:
        return (self.completed.stdout or "") + (self.completed.stderr or "")


def route_once(stack) -> RouteRun:
    """Run the compiled maintenance process for exactly the route_request kind."""
    completed, _reports = f00b.maintenance_once(stack, "--kinds", "route_request")
    return RouteRun(completed=completed, before_jobs=[])


def route_jobs(dbname: str, request_id: str) -> list[dict]:
    return f08.background_jobs(dbname, target_id=request_id, kind="route_request")


# ---------------------------------------------------------------------------
# authoritative routing rows
# ---------------------------------------------------------------------------
def candidate_rows(dbname: str, request_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT request_id::text AS request_id, "
        "application_version_id::text AS application_version_id, rank, "
        "application_name, version_no, capability_card::text AS capability_card, "
        "card_sha256, visible_at::text AS visible_at "
        "FROM platform.request_routing_candidates "
        f"WHERE request_id = {st.quote_literal(request_id)}::uuid ORDER BY rank",
    )


def decision_rows(dbname: str, request_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT request_id::text AS request_id, attempt, decision, "
        "selected_version_id::text AS selected_version_id, "
        "model_call_id::text AS model_call_id, raw_decision::text AS raw_decision, "
        "decided_at::text AS decided_at "
        "FROM platform.request_routing_decisions "
        f"WHERE request_id = {st.quote_literal(request_id)}::uuid ORDER BY attempt",
    )


def control_calls(dbname: str, request_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT c.id::text AS id, c.background_job_id::text AS background_job_id, "
        "c.request_id::text AS request_id, c.model_profile_version_id::text AS model_profile_version_id, "
        "c.logical_key, c.physical_attempt, c.state, "
        "c.result_artifact_id::text AS result_artifact_id "
        "FROM platform.platform_model_calls AS c "
        "JOIN platform.background_jobs AS j ON j.id = c.background_job_id "
        f"WHERE j.kind = 'route_request' AND j.target_id = {st.quote_literal(request_id)}::uuid "
        "ORDER BY c.created_at, c.id",
    )


def pending_interaction_row(dbname: str, request_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, state, correlation_id::text AS correlation_id, "
        "proposal_artifact_id::text AS proposal_artifact_id, "
        "response_artifact_id::text AS response_artifact_id, "
        "expires_at::text AS expires_at, answered_at::text AS answered_at "
        "FROM platform.interactions "
        f"WHERE request_id = {st.quote_literal(request_id)}::uuid AND state = 'pending'",
    )
    return rows[0] if rows else None


def interaction_rows(dbname: str, request_id: str) -> list[dict]:
    return f08.interaction_rows(dbname, request_id)


def proposal_bytes(stack, request_id: str) -> dict:
    """Read the pending interaction's sealed proposal through the object store."""
    row = pending_interaction_row(stack.dbname, request_id)
    assert row is not None, "no pending interaction"
    content = f08.artifact_bytes(stack, row["proposal_artifact_id"])
    return json.loads(content.decode("utf-8"))


def expire_interaction(stack, request_id: str, *, seconds: int = 5) -> str:
    """Move the pending interaction's deadline into the past (>= 5 s margin)."""
    row = pending_interaction_row(stack.dbname, request_id)
    assert row is not None, "no pending interaction to expire"
    st.sql(
        stack.dbname,
        "UPDATE platform.interactions SET expires_at = now() - make_interval(secs => "
        f"{seconds}) WHERE id = {st.quote_literal(row['id'])}::uuid",
    )
    return row["id"]


def request_state(dbname: str, request_id: str) -> dict:
    row = f08.request_row(dbname, request_id)
    assert row is not None, request_id
    return row


def wait_for_request_state(dbname: str, request_id: str, state: str, *, timeout: float = 10.0) -> dict:
    def check():
        row = f08.request_row(dbname, request_id)
        return row if row and row["state"] == state else None

    return f00b.wait_until(check, timeout=timeout, what=f"request {request_id} -> {state}")
