"""F06/T045 real-stack helpers: compatible fallback after availability faults.

The stack is T041's: a throwaway PostgreSQL database migrated through the real
``cmd/admin migrate`` chain (so migration 0027 runs), the compiled ``cmd/api``
behind real HTTP, the real internal mTLS listener, the real filesystem object
store and the real T025 golden manifest. The model provider is the documented
dev stub; a profile's registered ``model_id`` selects a deterministic fault
(``stub-unavailable*``, ``stub-rate-limited*``, ``stub-permission-denied*``), so
the bounded-retry and compatible-fallback path is exercised end to end without
inventing a vendor adapter.
"""

from __future__ import annotations

import json
from decimal import Decimal

from support import stack as st
from support import t025, t029, t041


def seed_profile(
    dbname: str,
    owner_id: str,
    *,
    model_id: str = "stub-model",
    config: dict | None = None,
    validation_state: str = "passed",
    revoked: bool = False,
) -> str:
    """Seed one real model profile version with a chosen model id and config.

    The default config is a chat-compatible generation profile, so a test that
    wants an *incompatible* fallback must say so explicitly.
    """
    profile_id = t025.seed_model_version(
        dbname, owner_id, validation_state=validation_state, revoked=revoked
    )
    st.sql(
        dbname,
        "UPDATE platform.model_profile_versions SET "
        f"model_id = {st.quote_literal(model_id)}, "
        f"config = {st.quote_literal(json.dumps(config if config is not None else compatible_config()))}::jsonb "
        f"WHERE id = {st.quote_literal(profile_id)}::uuid",
    )
    return profile_id


def compatible_config(fallback_ids: list[str] | None = None, **overrides) -> dict:
    """A generation/chat_compatible ModelConfig that can serve a chat call."""
    config: dict = {
        "kind": "generation",
        "protocol": "chat_compatible",
        "context_window": 200000,
        "max_output_tokens": 4096,
        "timeout_ms": 30000,
        "price": {
            "currency": "USD",
            "input_per_million": "1",
            "output_per_million": "2",
        },
    }
    if fallback_ids is not None:
        config["fallback_profile_version_ids"] = list(fallback_ids)
    config.update(overrides)
    return config


def set_profile(dbname: str, profile_id: str, *, model_id: str | None = None, config: dict | None = None) -> None:
    assignments = []
    if model_id is not None:
        assignments.append("model_id = " + st.quote_literal(model_id))
    if config is not None:
        assignments.append("config = " + st.quote_literal(json.dumps(config)) + "::jsonb")
    if not assignments:
        return
    st.sql(
        dbname,
        "UPDATE platform.model_profile_versions SET "
        + ", ".join(assignments)
        + f" WHERE id = {st.quote_literal(profile_id)}::uuid",
    )


def set_model_id(dbname: str, profile_id: str, model_id: str) -> None:
    set_profile(dbname, profile_id, model_id=model_id)


def set_run_budget(dbname: str, run_id: str, amount: str) -> None:
    st.sql(
        dbname,
        "UPDATE platform.runs SET budget_limit = "
        f"{amount}::numeric WHERE id = {st.quote_literal(run_id)}::uuid",
    )


def manifest_document(stack, run_id: str) -> dict:
    """The sealed execution manifest of one run, read from the real object bytes."""
    state = t029.run_state_row(stack.dbname, run_id)
    artifact_id = st.sql_scalar(
        stack.dbname,
        "SELECT manifest_artifact_id::text FROM platform.execution_snapshots WHERE id = "
        f"{st.quote_literal(state['execution_snapshot_id'])}::uuid",
    )
    artifact = t029.artifact_row(stack.dbname, artifact_id)
    return json.loads((stack.object_dir / artifact["object_key"]).read_bytes())


def operation_fallback(dbname: str, run_id: str, operation_id: str) -> dict:
    """The T045 columns of one operation row (never the provider payload)."""
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, state, capability_version, capability_kind, last_error_code, "
        "fallback_profile_version_id::text, fallback_reason "
        "FROM platform.operations "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
        f"AND id = {st.quote_literal(operation_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def attempt_cost_totals(dbname: str, operation_id: str) -> dict:
    """Sum the physical attempt ledger (T045 cost display truth)."""
    return st.sql_rows(
        dbname,
        "SELECT COUNT(*)::text AS attempts, "
        "COALESCE(SUM(tokens_in), 0)::text AS tokens_in, "
        "COALESCE(SUM(tokens_out), 0)::text AS tokens_out, "
        "COALESCE(SUM(charged_amount), 0)::text AS charged_amount "
        "FROM platform.operation_attempts "
        f"WHERE operation_id = {st.quote_literal(operation_id)}::uuid",
    )[0]


def same_amount(left: str, right: str) -> bool:
    return Decimal(str(left)) == Decimal(str(right))
