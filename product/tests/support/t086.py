"""F04B/T086 real-stack helpers: the single-generation model node.

Everything here goes through the product's real path: a real draft saved through
the public API (compiled by the Go workflow compiler into the frozen manifest), the
real debug submission, the compiled ``cmd/supervisor exec --once`` claiming the
attempt, the real T041 capability proxy (worker mTLS + live attempt token) against
the documented dev-stub model provider, the real PostgreSQL ledger and the
owner-only reads.

Nothing is mocked. The model answer is the deterministic dev stub (labelled stub);
the capability proxy, the operation rows, the budget ledger and the sealed
artifacts are real. A model-only workflow starts no sandbox, so the run's
deterministic failures are the graph path's documented "refused" shape.
"""

from __future__ import annotations

import json

from support import stack as st
from support import t025, t027, t033, t042

GOLDEN_MODEL_ID = t025.GOLDEN_MODEL_ID

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

TEXT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

TASK_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"task": {"type": "string"}},
    "required": ["task"],
    "additionalProperties": False,
}


def model_definition(
    model_id: str,
    *,
    mode: str = "json",
    template: str = "Summarize {{task}}",
    node_schema: dict | None = None,
    input_schema: dict | None = None,
    execution_output_schema: dict | None = None,
) -> dict:
    """start -> analyse(model, mode) -> end with explicit bindings.

    ``node_schema`` is the model node's output_schema ("the response schema");
    ``execution_output_schema`` is the application's output declaration (what the
    end node maps). They are separate on purpose: a schema failure case can keep
    the application output legal while the node's own contract is stricter.
    """
    if mode == "json":
        node_output = node_schema or dict(SUMMARY_SCHEMA)
        end_inputs = {"summary": {"ref": "node:analyse#/summary"}}
        default_execution = dict(SUMMARY_SCHEMA)
    else:
        node_output = node_schema or dict(TEXT_SCHEMA)
        end_inputs = {"text": {"ref": "node:analyse#/text"}}
        default_execution = dict(TEXT_SCHEMA)

    model_node = {
        "id": "analyse",
        "type": "model",
        "inputs": {"task": {"ref": "input#/task"}},
        "output_schema": node_output,
        "config": {
            "model_profile_version": model_id,
            "prompt_template": template,
            "response_mode": mode,
        },
    }
    end_node = t027.end_node()
    end_node["inputs"] = dict(end_inputs)
    return {
        "dsl_version": "1.0",
        "input_schema": input_schema or dict(TASK_INPUT_SCHEMA),
        "output_schema": execution_output_schema or default_execution,
        "nodes": [t027.start_node(), model_node, end_node],
        "edges": [
            {"source": "start", "target": "analyse"},
            {"source": "analyse", "target": "end"},
        ],
        "policy": dict(t027.BASE_POLICY),
    }


def application_for(stack, definition: dict, *, name: str = "t086 model"):
    owner = t033.golden_owner(stack)
    application_id = t027.application_with_definition(owner.creator, definition, name=name)
    return owner, application_id


def queue_model_run(stack, definition: dict, *, input_value: dict, name: str = "t086 model"):
    owner, application_id = application_for(stack, definition, name=name)
    run = t042.submit_linear_run(owner, application_id, artifact_id=None, input_value=input_value)
    return owner, application_id, run


def run_once(stack, *, env_extra: dict[str, str] | None = None):
    return t042.run_linear_once(stack, env_extra=env_extra)


# ---------------------------------------------------------------------------
# database reads
# ---------------------------------------------------------------------------
def operations(dbname: str, run_id: str) -> list[dict]:
    """Every capability operation of the run (one per logical model call)."""
    return st.sql_rows(
        dbname,
        "SELECT id::text, logical_key, capability_version::text, capability_kind, "
        "state, receipt_artifact_id::text, last_error_code "
        "FROM platform.operations "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
        "ORDER BY logical_key",
    )


def operation_attempts(dbname: str, run_id: str) -> list[dict]:
    """The physical attempts of the run's operations (never a hidden retry)."""
    return st.sql_rows(
        dbname,
        "SELECT oa.operation_id::text, oa.attempt_no, oa.state, oa.settlement_state, "
        "oa.tokens_in, oa.tokens_out, oa.charged_amount::text "
        "FROM platform.operation_attempts AS oa "
        "JOIN platform.operations AS o ON o.id = oa.operation_id "
        f"WHERE o.run_id = {st.quote_literal(run_id)}::uuid "
        "ORDER BY o.logical_key, oa.attempt_no",
    )


def capability_params_document(stack, run_id: str) -> dict:
    """The sealed canonical params of the run's one model operation.

    It is read from the real object bytes the capability proxy sealed, so a test
    can prove exactly what left the executor (the prompt is user content, but it
    is sealed capability params, not a public event or manifest field).
    """
    rows = st.sql_rows(
        stack.dbname,
        "SELECT a.object_key "
        "FROM platform.run_artifacts AS ra "
        "JOIN platform.artifacts AS a ON a.id = ra.artifact_id "
        f"WHERE ra.run_id = {st.quote_literal(run_id)}::uuid "
        "AND a.purpose = 'capability_params' "
        "ORDER BY a.object_key",
    )
    assert len(rows) == 1, rows
    return json.loads((stack.object_dir / rows[0]["object_key"]).read_bytes())


def receipt_document(stack, run_id: str) -> dict:
    """The sealed capability receipt of the run's one model operation."""
    rows = st.sql_rows(
        stack.dbname,
        "SELECT a.object_key "
        "FROM platform.operations AS o "
        "JOIN platform.artifacts AS a ON a.id = o.receipt_artifact_id "
        f"WHERE o.run_id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return json.loads((stack.object_dir / rows[0]["object_key"]).read_bytes())


def revoke_profile(dbname: str, profile_id: str) -> None:
    """Revoke a profile after the run was sealed (the executor must re-check)."""
    st.sql(
        dbname,
        "UPDATE platform.model_profile_versions SET revoked_at = now() "
        f"WHERE id = {st.quote_literal(profile_id)}::uuid",
    )


def node_data_rows(dbname: str, run_id: str) -> list[dict]:
    return t042.node_result_rows(dbname, run_id)


def count_runs_for_application(dbname: str, application_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.runs AS r "
            "JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id "
            f"WHERE s.application_id = {st.quote_literal(application_id)}::uuid",
        )
    )
