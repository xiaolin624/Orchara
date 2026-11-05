"""F07/T080 real-stack helpers: parallel child calls share one parent allowance.

The stack is T029's (throwaway PostgreSQL migrated by the real ``cmd/admin``
chain, the compiled ``cmd/api`` behind real HTTP, the real filesystem object
store and the real internal mTLS listener) plus the supervisor binary. Nothing is
mocked:

* a child Run is always created by the contract ``spawnChild`` operation;
* a model call is a real ``/internal/v1/runs/{id}/capabilities`` request on the
  worker mTLS capacity, and its reservation/settlement are real
  ``platform.usage_ledger`` rows;
* a concurrency gate is a real PostgreSQL row lock on the *budget root* (the
  parent Run) held by a real ``psql`` session -- exactly the row
  ``internal/budget.ReserveTx`` locks before it re-reads the tree totals, so the
  test never decides a race with a wall-clock sleep.
"""

from __future__ import annotations

import re
import threading
from decimal import Decimal

from support import t025, t029, t038, t041, t045, t046, t075, t078
from support import stack as st

USD = "USD"
ONE_UNIT = Decimal("0.00000001")
AMOUNT_TEXT = re.compile(r"^\d+\.\d{8}$")


# ---------------------------------------------------------------------------
# priced model + model-capable child version
# ---------------------------------------------------------------------------
def seed_priced_golden(stack, session) -> str:
    """Seed the golden model profile as a *priced* generation profile.

    The child version sealed below pins this exact profile id, so a capability
    call from the child Run is authorized by the child's own frozen manifest and
    priced by the same profile the T046 budget tests use.
    """
    t025.seed_model_version(stack.dbname, session.principal_id, model_id=t025.GOLDEN_MODEL_ID)
    t046.configure_golden(stack.dbname, config=t045.compatible_config())
    return t025.GOLDEN_MODEL_ID


def priced_config(fallback_ids: list[str] | None = None) -> dict:
    return t045.compatible_config(fallback_ids)


def model_child_version(stack, session, *, model_id: str | None = None, version_no: int = 1) -> dict:
    """A start -> model -> end child version whose frozen manifest pins the model.

    ``model`` nodes are not executable by the real supervisor (F04B owns that
    node), so this shape is used only with a *direct* capability invocation on
    the child's live attempt, exactly like the T046 budget tests drive a run.
    """
    definition = t075.child_definition(model_id or t025.GOLDEN_MODEL_ID)
    return t078.create_child_version(stack, session, version_no=version_no, definition=definition)


def agent_child_definition(model_version_id: str) -> dict:
    """start -> agent -> end: one real model capability call, no tools, no subapps.

    The agent node is the executable shape of a model call (``model`` nodes belong
    to F04B), so a supervis-executed child with a tiny parent budget really reaches
    ``internal/budget.ReserveTx`` and is refused there.
    """
    return {
        "dsl_version": "1.0",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
        "output_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "nodes": [
            {
                "id": "start", "type": "start", "inputs": {},
                "output_schema": {"type": "object"}, "config": {},
            },
            {
                "id": "analysis",
                "type": "agent",
                "inputs": {},
                "output_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "config": {
                    "model_profile_version": model_version_id,
                    "instructions": "Answer in one short sentence.",
                    "max_turns": 2,
                    "tool_versions": [],
                    "subapp_versions": [],
                },
            },
            {
                "id": "end", "type": "end",
                "inputs": {"text": {"ref": "node:analysis#/text"}},
                "output_schema": {"type": "object"},
                "config": {},
            },
        ],
        "edges": [
            {"source": "start", "target": "analysis"},
            {"source": "analysis", "target": "end"},
        ],
        "policy": {
            "budget_limit": "1.00",
            "currency": "USD",
            "max_active_seconds": 300,
            "max_child_calls": 12,
            "max_child_depth": 3,
        },
    }


def agent_child_version(stack, session, *, model_id: str | None = None, version_no: int = 1) -> dict:
    definition = agent_child_definition(model_id or t025.GOLDEN_MODEL_ID)
    return t078.create_child_version(stack, session, version_no=version_no, definition=definition)


def parent_definition(
    child_version_id: str,
    *,
    output_key: str = "echo",
    max_child_calls: int = 12,
    max_child_depth: int = 3,
) -> dict:
    return t078.parent_definition(
        [child_version_id],
        output_key=output_key,
        max_child_calls=max_child_calls,
        max_child_depth=max_child_depth,
    )


def debug_parent(
    stack, session, child_version_id: str, *, input_value: dict | None = None,
    output_key: str = "echo", **limits,
):
    definition = parent_definition(child_version_id, output_key=output_key, **limits)
    return t078.debug_parent(
        stack, session, definition, input_value or {"source": "parent-source"}
    )


# ---------------------------------------------------------------------------
# leases and the real admin recovery command
# ---------------------------------------------------------------------------
def set_capacity(stack, capacity: int = 8) -> None:
    t029.set_quota_capacity(stack.dbname, capacity)


def claim(stack, *, run_id: str | None = None, unit: str = "t080-unit") -> dict:
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t080-supervisor", execution_unit_id=unit
    )
    assert response.status == 200, response.text
    lease = response.json
    if run_id is not None:
        assert lease["run_id"] == run_id, (lease["run_id"], run_id)
    return lease


def expire_and_park(stack, lease: dict) -> dict:
    """Age the lease + heartbeat, then park the Run with the real sweep."""
    t038.expire_lease(stack.dbname, lease["attempt_token"])
    _completed, report = t038.sweep(stack, "--grace", "1s")
    assert report["parked"] >= 1, report
    row = t029.run_state_row(stack.dbname, lease["run_id"])
    assert row["state"] == "recovery_pending", row
    return report


# ---------------------------------------------------------------------------
# budget facts (all reads are the authoritative PostgreSQL rows)
# ---------------------------------------------------------------------------
def root_run_id(dbname: str, run_id: str) -> str:
    return st.sql_scalar(
        dbname,
        "WITH RECURSIVE chain AS ("
        "  SELECT id, parent_run_id FROM platform.runs "
        f"  WHERE id = {st.quote_literal(run_id)}::uuid "
        "  UNION ALL "
        "  SELECT r.id, r.parent_run_id FROM platform.runs AS r "
        "  JOIN chain AS c ON r.id = c.parent_run_id) "
        "SELECT id::text FROM chain WHERE parent_run_id IS NULL LIMIT 1",
    )


def ledger_rows_at_root(dbname: str, root_run_id_value: str) -> list[dict]:
    """Every ledger row of the tree, keyed by ``budget_root_run_id``."""
    return st.sql_rows(
        dbname,
        "SELECT id::text, run_id::text, budget_root_run_id::text, operation_id::text, "
        "entry_key, kind, amount::text, currency, tokens_in, tokens_out, "
        "price_version, created_at::text "
        "FROM platform.usage_ledger "
        f"WHERE budget_root_run_id = {st.quote_literal(root_run_id_value)}::uuid "
        "ORDER BY created_at, entry_key",
    )


def ledger_totals_at_root(dbname: str, root_run_id_value: str) -> dict:
    return st.sql_rows(
        dbname,
        "SELECT "
        "COALESCE(SUM(CASE WHEN kind = 'reserve' THEN amount ELSE 0 END), 0)::text AS reserved, "
        "COALESCE(SUM(CASE WHEN kind = 'release' THEN amount ELSE 0 END), 0)::text AS released, "
        "COALESCE(SUM(CASE WHEN kind = 'charge' THEN amount ELSE 0 END), 0)::text AS charged, "
        "COALESCE(SUM(CASE WHEN kind = 'reserve' THEN amount "
        "WHEN kind = 'release' THEN -amount ELSE 0 END), 0)::text AS outstanding "
        "FROM platform.usage_ledger "
        f"WHERE budget_root_run_id = {st.quote_literal(root_run_id_value)}::uuid",
    )[0]


def money_columns(dbname: str) -> dict[str, dict]:
    return {
        (row["table_name"], row["column_name"]): row
        for row in st.sql_rows(
            dbname,
            "SELECT table_name, column_name, data_type, udt_name, numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema = 'platform' AND data_type = 'numeric' "
            "AND ((table_name = 'usage_ledger' AND column_name = 'amount') "
            "  OR (table_name = 'runs' AND column_name = 'budget_limit'))",
        )
    }


def reservation(params: dict, *, input_per_million: str = "1", output_per_million: str = "2") -> Decimal:
    return t046.expected_reservation(
        params, input_per_million=input_per_million, output_per_million=output_per_million
    )


def expected_charge(tokens_in: int, tokens_out: int, *, input_per_million: str = "1",
                    output_per_million: str = "2") -> Decimal:
    return t046.expected_charge(
        tokens_in, tokens_out, input_per_million=input_per_million,
        output_per_million=output_per_million,
    )


def set_parent_budget(dbname: str, run_id: str, amount) -> None:
    t045.set_run_budget(dbname, run_id, str(amount))


def run_budget(dbname: str, run_id: str) -> tuple[str, str]:
    row = t078.run_row(dbname, run_id)
    return row["budget_limit"], row["currency"]


def count_rows(dbname: str, table: str) -> int:
    return int(st.sql(dbname, f"SELECT count(*) FROM platform.{table}"))


def run_input_artifact_count(dbname: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.artifacts WHERE purpose = 'run_input'",
        )
    )


# ---------------------------------------------------------------------------
# concurrent execution behind a real PostgreSQL row-lock gate
# ---------------------------------------------------------------------------
def race_spawns(
    stack,
    parent_run_id: str,
    *,
    attempt_token: str,
    application_version_id: str,
    logical_keys: list[str],
    gate_run_id: str | None = None,
    inputs: list[dict] | None = None,
) -> list:
    """Start N real spawnChild requests together behind a row lock on the parent."""
    results: list[object] = [None] * len(logical_keys)
    barrier = threading.Barrier(len(logical_keys) + 1, timeout=30)
    inputs = inputs or [{"source": f"source-{index}"} for index in range(len(logical_keys))]

    def worker(index: int) -> None:
        client = t078.children_client(stack, "worker")
        barrier.wait()
        try:
            results[index] = client.spawn_child(
                parent_run_id,
                attempt_token=attempt_token,
                logical_key=logical_keys[index],
                application_version_id=application_version_id,
                input=inputs[index],
            )
        except Exception as error:  # pragma: no cover - surfaced by the assertion
            results[index] = error

    _race_under_gate(
        stack,
        gate_run_id or parent_run_id,
        [(worker, (index,)) for index in range(len(logical_keys))],
        barrier,
    )
    for result in results:
        assert result is not None and not isinstance(result, Exception), result
    return results


def race_child_invocations(
    stack,
    leases: list[dict],
    *,
    params: dict,
    operation_ids: list[str],
    gate_run_id: str,
    logical_keys: list[str] | None = None,
) -> list:
    """Start N real model capability calls from N child Runs behind one gate.

    The gate is the budget root's row: ``internal/budget.ReserveTx`` locks it
    before it re-reads ``sum(reserve)-sum(release)``, so both requests really are
    at the same decision point when the gate is released.
    """
    assert len(leases) == len(operation_ids)
    logical_keys = logical_keys or [f"race-{index + 1}" for index in range(len(leases))]
    results: list[object] = [None] * len(leases)
    barrier = threading.Barrier(len(leases) + 1, timeout=30)

    def worker(index: int) -> None:
        client = t041.worker_client(stack)
        barrier.wait()
        try:
            results[index] = t041.invoke(
                client, leases[index], operation_id=operation_ids[index],
                logical_key=logical_keys[index], params=params,
            )
        except Exception as error:  # pragma: no cover - surfaced by the assertion
            results[index] = error

    _race_under_gate(
        stack,
        gate_run_id,
        [(worker, (index,)) for index in range(len(leases))],
        barrier,
    )
    for result in results:
        assert result is not None and not isinstance(result, Exception), result
    return results


def _race_under_gate(stack, gate_run_id: str, work: list, barrier) -> None:
    threads = [threading.Thread(target=target, args=args) for target, args in work]
    with t046.RunRowGate(stack.dbname, gate_run_id) as gate:
        for thread in threads:
            thread.start()
        barrier.wait()
        gate.wait_for_waiters(len(threads))
        gate.release()
        for thread in threads:
            thread.join(timeout=60)
            assert not thread.is_alive(), "a raced request did not finish"


__all__ = [name for name in dir() if not name.startswith("_")]
