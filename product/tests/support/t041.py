"""F06/T041 real-stack helpers: the run-scoped capability proxy.

The stack is the T039/F06 one: a throwaway PostgreSQL database migrated with the
real ``cmd/admin migrate`` chain (so migration 0025 runs), the compiled ``cmd/api``
behind real HTTP, the real internal mTLS listener, the real filesystem object store
and the real T025 golden manifest. Nothing is mocked: the capability call is a real
mTLS HTTP request with a real worker certificate and the run's live attempt token,
and every ledger assertion reads PostgreSQL rows and the sealed object bytes.

The run is created through the only production entry this batch has
(``debugDraft``) and claimed through the real T029 supervisor claim. Real
sandboxed node execution is T042/T043; the test worker here executes a real local
tool function and reports its result through the non-contract endpoint.
"""

from __future__ import annotations

import json

from support import f00b, t024, t025, t027, t029
from support import stack as st

GOLDEN_MODEL_ID = t025.GOLDEN_MODEL_ID
GOLDEN_TOOL_ID = t025.GOLDEN_TOOL_ID
GOLDEN_APPLICATION_ID = t025.GOLDEN_APPLICATION_ID

# A simple declared tool schema used by the tool-call tests. The golden manifest
# pins the tool version id, not its input schema, so updating the row is setup
# (the tool row is a real database fact the capability validator reads).
CITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["city"],
    "properties": {"city": {"type": "string"}},
}


def owner(stack, *, login: str | None = None) -> t029.Owner:
    return t029.golden_owner(stack, login=login)


def secondary_owner(stack) -> t029.Owner:
    """Another real creator with no access to the first owner's run."""
    return t029.minimal_owner(stack)


def claimed_run(stack, run_owner: t029.Owner | None = None) -> tuple[t029.Owner, dict, dict]:
    """Create one debug run and claim it as the real supervisor."""
    run_owner = run_owner or owner(stack)
    run = t029.queued_run(stack, run_owner)
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t041-supervisor", execution_unit_id="t041-unit"
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run["id"], (lease, run)
    return run_owner, run, lease


def worker_client(stack) -> f00b.InternalClient:
    return t029.execution_client(stack, "worker")


def model_params(
    *,
    messages: list[dict] | None = None,
    response_schema: dict | None = None,
    tools: list[dict] | None = None,
    **extra,
) -> dict:
    params: dict = {
        "task": "agent",
        "messages": messages
        or [{"role": "user", "content": "Summarize the sales file."}],
    }
    if response_schema is not None:
        params["response_schema"] = response_schema
    if tools is not None:
        params["tools"] = tools
    params.update(extra)
    return params


def invoke(
    client: f00b.InternalClient,
    lease: dict,
    *,
    operation_id: str,
    logical_key: str = "call-1",
    capability_version: str = GOLDEN_MODEL_ID,
    capability_kind: str = "model",
    params: dict | None = None,
    attempt_token: str | None = None,
    body: dict | None = None,
):
    if body is None:
        body = {
            "attempt_token": lease["attempt_token"] if attempt_token is None else attempt_token,
            "operation_id": operation_id,
            "logical_key": logical_key,
            "capability_version": capability_version,
            "params": params,
            "capability_kind": capability_kind,
        }
    return client.post(f"/internal/v1/runs/{lease['run_id']}/capabilities", body)


def report_result(
    client: f00b.InternalClient,
    lease: dict,
    *,
    operation_id: str,
    state: str = "succeeded",
    data: dict | None = None,
    result_artifact_id: str | None = None,
    error_code: str | None = None,
    attempt_token: str | None = None,
):
    body: dict = {
        "attempt_token": lease["attempt_token"] if attempt_token is None else attempt_token,
        "operation_id": operation_id,
        "state": state,
    }
    if data is not None:
        body["data"] = data
    if result_artifact_id is not None:
        body["result_artifact_id"] = result_artifact_id
    if error_code is not None:
        body["error_code"] = error_code
    return client.post(f"/internal/v1/runs/{lease['run_id']}/capability-results", body)


# ---------------------------------------------------------------------------
# database reads
# ---------------------------------------------------------------------------
def operation_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text, run_id::text, logical_key, capability_version, capability_kind, "
        "risk_class, params_sha256, params_artifact_id::text, state, "
        "receipt_artifact_id::text, last_error_code, "
        "dispatched_at::text, created_at::text, updated_at::text "
        "FROM platform.operations "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY created_at, id",
    )


def operation_row(dbname: str, run_id: str, operation_id: str) -> dict | None:
    rows = [
        row
        for row in operation_rows(dbname, run_id)
        if row["id"] == operation_id
    ]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def attempt_rows(dbname: str, operation_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text, operation_id::text, attempt_no, state, attempt_token::text, "
        "result_artifact_id::text, error_code, tokens_in, tokens_out, "
        "reserved_amount::text, charged_amount::text, currency, price_version, "
        "settlement_state, created_at::text, finished_at::text "
        "FROM platform.operation_attempts "
        f"WHERE operation_id = {st.quote_literal(operation_id)}::uuid ORDER BY attempt_no",
    )


def attempt_count(dbname: str, operation_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.operation_attempts WHERE "
            f"operation_id = {st.quote_literal(operation_id)}::uuid",
        )
    )


def artifact_row(dbname: str, artifact_id: str) -> dict:
    return t029.artifact_row(dbname, artifact_id)


def artifact_bytes(stack, artifact: dict) -> bytes:
    return (stack.object_dir / artifact["object_key"]).read_bytes()


def receipt_document(stack, artifact_id: str) -> dict:
    artifact = artifact_row(stack.dbname, artifact_id)
    return json.loads(artifact_bytes(stack, artifact))


def set_tool_schema(dbname: str, schema: dict, *, tool_id: str = GOLDEN_TOOL_ID) -> None:
    st.sql(
        dbname,
        "UPDATE platform.tool_versions SET input_schema = "
        f"{st.quote_literal(json.dumps(schema))}::jsonb "
        f"WHERE id = {st.quote_literal(tool_id)}::uuid",
    )


def set_model_config(dbname: str, config: dict, *, model_id: str = GOLDEN_MODEL_ID) -> None:
    st.sql(
        dbname,
        "UPDATE platform.model_profile_versions SET config = "
        f"{st.quote_literal(json.dumps(config))}::jsonb "
        f"WHERE id = {st.quote_literal(model_id)}::uuid",
    )


def revoke_model(dbname: str, *, model_id: str = GOLDEN_MODEL_ID) -> None:
    st.sql(
        dbname,
        "UPDATE platform.model_profile_versions SET revoked_at = now() "
        f"WHERE id = {st.quote_literal(model_id)}::uuid",
    )


def cancel_run(dbname: str, run_id: str) -> None:
    t029.set_cancel_requested(dbname, run_id)


def run_state(dbname: str, run_id: str) -> dict:
    return t029.run_state_row(dbname, run_id)


# ---------------------------------------------------------------------------
# public ledger read
# ---------------------------------------------------------------------------
def ledger(session: f00b.Session, run_id: str) -> st.Response:
    return session.client.get(f"/api/v1/runs/{run_id}/capabilities")


def ledger_items(session: f00b.Session, run_id: str) -> list[dict]:
    response = ledger(session, run_id)
    assert response.status == 200, response.text
    body = response.json
    # T046 adds the decimal budget projection beside the operation items.
    assert set(body) <= {"items", "budget"}, body
    assert "items" in body, body
    return body["items"]


def ledger_budget(session: f00b.Session, run_id: str) -> dict:
    """F06/T046: the decimal budget projection of the same owner read."""
    response = ledger(session, run_id)
    assert response.status == 200, response.text
    assert "budget" in response.json, response.json
    return response.json["budget"]


def new_uuid() -> str:
    return f00b.new_uuid()


def manifest_sha(dbname: str, run_id: str) -> str:
    return t029.run_state_row(dbname, run_id)["execution_snapshot_id"]
