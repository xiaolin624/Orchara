"""Shared helpers for the F06/T039 model-registry tests.

Everything here talks to the real API over HTTP; the only database reads are the
assertions about rows a real request or a real maintenance process wrote.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from support import f00b, t024
from support import stack as st

# The documented dev-stub catalog (both environment variables unset).
DEFAULT_ENDPOINT = "stub-endpoint"
DEFAULT_CREDENTIAL = "stub-credential"
DEFAULT_DESTINATION = "local-stub"

PROFILE_KEYS = {
    "id",
    "name",
    "model_id",
    "validation_state",
    "data_destination",
    "evaluation_report_id",
}
EVALUATION_KEYS = {"id", "kind", "state", "background_job_id", "report_id", "error_code"}


def config(**overrides) -> dict:
    value = {
        "kind": "generation",
        "protocol": "chat_compatible",
        "context_window": 8192,
        "max_output_tokens": 1024,
        "timeout_ms": 30000,
        "price": {
            "currency": "USD",
            "input_per_million": "0",
            "output_per_million": "0",
        },
    }
    value.update(overrides)
    return value


def registration_body(**overrides) -> dict:
    body = {
        "name": "candidate " + secrets.token_hex(6),
        "provider": "stub",
        "model_id": "stub-model-1",
        "endpoint_ref": DEFAULT_ENDPOINT,
        "secret_ref": DEFAULT_CREDENTIAL,
        "config": config(),
        "data_destination": DEFAULT_DESTINATION,
    }
    body.update(overrides)
    return body


def register(session: f00b.Session, **overrides) -> st.Response:
    return session.client.post(
        "/api/v1/models", body=registration_body(**overrides), csrf=session.csrf
    )


def register_or_fail(session: f00b.Session, **overrides) -> dict:
    response = register(session, **overrides)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {"id", "state"}, body
    assert body["state"] == "pending", body
    return body


def validate(session: f00b.Session, model_id: str, key: str | None = None) -> st.Response:
    headers = {} if key is None else {"Idempotency-Key": key}
    return session.client.request(
        "POST",
        f"/api/v1/models/{model_id}/validate",
        csrf=session.csrf,
        headers=headers,
    )


def get_model(session: f00b.Session, model_id: str) -> st.Response:
    return session.client.get(f"/api/v1/models/{model_id}")


def list_models(session: f00b.Session, query: str = "") -> st.Response:
    suffix = ("?" + query) if query else ""
    return session.client.get("/api/v1/models" + suffix)


def model_row(dbname: str, model_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, name, provider, model_id, endpoint_ref, secret_ref, "
        "data_destination, manifest_sha256, manifest_artifact_id::text, "
        "validation_state, evaluation_report_id::text, revoked_at "
        "FROM platform.model_profile_versions "
        f"WHERE id = {st.quote_literal(model_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def evaluation_for_model(dbname: str, model_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, kind, state, background_job_id::text, report_id::text, "
        "dataset_artifact_id::text, error_code "
        "FROM platform.evaluation_jobs "
        f"WHERE model_profile_version_id = {st.quote_literal(model_id)}::uuid "
        "ORDER BY created_at DESC",
    )
    return rows[0] if rows else {}


def report_row(dbname: str, report_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, manifest_sha256, dataset_sha256, "
        "result_artifact_id::text, status FROM platform.evaluation_reports "
        f"WHERE id = {st.quote_literal(report_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def artifact_row(dbname: str, artifact_id: str) -> dict:
    return f00b.artifact_row(dbname, artifact_id)


def sealed_content(object_dir: Path, artifact: dict) -> bytes:
    return (object_dir / artifact["object_key"]).read_bytes()


def report_document(stack, report_id: str) -> dict:
    row = report_row(stack.dbname, report_id)
    artifact = artifact_row(stack.dbname, row["result_artifact_id"])
    return json.loads(sealed_content(stack.object_dir, artifact))


def run_validation(stack) -> tuple:
    """Run the real model_validate + artifact_finalize processes once."""
    return t024.run_model_validate_once(stack)


def model_definition(model_id: str) -> dict:
    policy = {
        "max_active_seconds": 900,
        "max_child_depth": 3,
        "max_child_calls": 12,
        "budget_limit": "1.00",
        "currency": "USD",
    }
    return {
        "dsl_version": "1.0",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "inputs": {},
                "output_schema": {"type": "object"},
                "config": {},
            },
            {
                "id": "analysis",
                "type": "model",
                "inputs": {"task": {"literal": "hello"}},
                "output_schema": {"type": "object"},
                "config": {
                    "model_profile_version": model_id,
                    "prompt_template": "{{task}}",
                    "response_mode": "text",
                },
            },
            {
                "id": "end",
                "type": "end",
                "inputs": {},
                "output_schema": {"type": "object"},
                "config": {},
            },
        ],
        "edges": [
            {"source": "start", "target": "analysis"},
            {"source": "analysis", "target": "end"},
        ],
        "policy": policy,
    }
