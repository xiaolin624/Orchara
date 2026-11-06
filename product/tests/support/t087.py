"""F04B/T087 real-stack helpers: the human approval node.

Everything here runs on the shared F04B/F06 stack (throwaway PostgreSQL migrated
through the real chain, the compiled ``cmd/api`` behind real HTTP, the real mTLS
internal listener, the real object store and HMAC-signed sealed links, the
compiled ``cmd/supervisor exec --once``). No approval, decision or resume is
mocked:

* ``POST /internal/v1/runs/{id}/node-approvals`` is the worker-capacity node
  approval route this ticket adds (non-contract);
* ``POST /api/v1/approvals/{id}/decision`` is the contract decision path;
* the approval row, the sealed proposal object and the run/job/waiting facts are
  read from PostgreSQL and the object root.

The helpers here are shared with ``tests/f04b/test_t087_approval_node.py``.
"""

from __future__ import annotations

import json
import uuid

from support import stack as st
from support import t025, t027, t029, t033, t035, t041, t042, t048, t050, t083

PRESET_TOOL_ID = t042.PRESET_TOOL_ID

# The node output contract this ticket documents (reports/f04b/T087-approval-node.md).
APPROVAL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "approval_id": {"type": "string"},
        "decision": {"type": "string"},
        "decided_at": {"type": "string"},
        "params_sha256": {"type": "string"},
        "proposal": {"type": "object"},
    },
    "required": ["approval_id", "decision", "decided_at", "params_sha256", "proposal"],
    "additionalProperties": False,
}

REJECTED_CODE = "approval_rejected"
EXPIRED_CODE = "approval_expired"

NODE_APPROVAL_PATH = "/internal/v1/runs/{run_id}/node-approvals"


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------
def approval_node(
    node_id: str,
    *,
    title: str = "Approve the order",
    expires_seconds: int = 3600,
    proposal: dict | None = None,
    proposal_ref: str | None = None,
) -> dict:
    """One approval node whose proposal is a bound input (literal or ref)."""
    if proposal_ref is not None:
        binding = {"ref": proposal_ref}
    else:
        binding = {
            "literal": proposal
            if proposal is not None
            else {"subject": "order-42", "amount": "1250.00"}
        }
    return {
        "id": node_id,
        "type": "approval",
        "inputs": {"proposal": binding},
        "output_schema": dict(APPROVAL_OUTPUT_SCHEMA),
        "config": {"title": title, "expires_seconds": expires_seconds},
    }


def approval_definition(
    *,
    title: str = "Approve the order",
    expires_seconds: int = 3600,
    proposal: dict | None = None,
) -> dict:
    """start -> approve1(approval) -> end, mapping the decision into the output."""
    end = t027.end_node()
    end["inputs"] = {
        "approval_id": {"ref": "node:approve1#/approval_id"},
        "decision": {"ref": "node:approve1#/decision"},
    }
    end["output_schema"] = {
        "type": "object",
        "properties": {"approval_id": {"type": "string"}, "decision": {"type": "string"}},
        "required": ["approval_id", "decision"],
        "additionalProperties": False,
    }
    definition = t027.definition(
        [
            t027.start_node(),
            approval_node(
                "approve1",
                title=title,
                expires_seconds=expires_seconds,
                proposal=proposal,
            ),
            end,
        ],
        [{"source": "start", "target": "approve1"},
         {"source": "approve1", "target": "end"}],
    )
    definition["output_schema"] = end["output_schema"]
    return definition


def approval_after_join_definition() -> dict:
    """start -> two python branches -> join -> approval -> end.

    The join runs in the first process, before the waiting approval. The second
    process resumes after the decision, so the durable join output must be reused
    instead of firing the join again. This is the real-process replay shape for
    the T085 join-once requirement.
    """
    end = t027.end_node()
    end["inputs"] = {
        "approval_id": {"ref": "node:approve1#/approval_id"},
        "decision": {"ref": "node:approve1#/decision"},
        "results": {"ref": "node:join#/results"},
    }
    end["output_schema"] = {
        "type": "object",
        "properties": {
            "approval_id": {"type": "string"},
            "decision": {"type": "string"},
            "results": {"type": "object"},
        },
        "required": ["approval_id", "decision", "results"],
        "additionalProperties": False,
    }
    definition = t027.definition(
        [
            t027.start_node(),
            t083.python_node("left"),
            t083.python_node("right"),
            t083.join_node(
                "join", fork_id="start", required=("left", "right")
            ),
            approval_node("approve1", title="Approve the joined analysis"),
            end,
        ],
        [
            {"source": "start", "target": "left"},
            {"source": "start", "target": "right"},
            {"source": "left", "target": "join"},
            {"source": "right", "target": "join"},
            {"source": "join", "target": "approve1"},
            {"source": "approve1", "target": "end"},
        ],
    )
    definition["output_schema"] = end["output_schema"]
    return definition


def two_approval_definition() -> dict:
    """start -> a1(approval) -> a2(approval) -> end (T091's rehearsal shape)."""
    end = t027.end_node()
    end["inputs"] = {
        "first": {"ref": "node:a1#/approval_id"},
        "second": {"ref": "node:a2#/approval_id"},
    }
    end["output_schema"] = {
        "type": "object",
        "properties": {"first": {"type": "string"}, "second": {"type": "string"}},
        "required": ["first", "second"],
        "additionalProperties": False,
    }
    definition = t027.definition(
        [
            t027.start_node(),
            approval_node("a1", title="First approval"),
            approval_node("a2", title="Second approval"),
            end,
        ],
        [{"source": "start", "target": "a1"},
         {"source": "a1", "target": "a2"},
         {"source": "a2", "target": "end"}],
    )
    definition["output_schema"] = end["output_schema"]
    return definition


def approval_then_python_definition() -> dict:
    """start -> approve1(approval) -> stats(python preset tool) -> end.

    The manual-claim write test never executes this graph; the preset python node
    exists so the compiled manifest freezes the tool closure (and with it the
    connector policies the later write capability must still request approval
    for).
    """
    python = t083.python_node("stats")
    end = t027.end_node()
    end["inputs"] = {"approval_id": {"ref": "node:approve1#/approval_id"}}
    definition = t027.definition(
        [t027.start_node(), approval_node("approve1"), python, end],
        [{"source": "start", "target": "approve1"},
         {"source": "approve1", "target": "stats"},
         {"source": "stats", "target": "end"}],
    )
    definition["output_schema"] = {"type": "object"}
    return definition


# ---------------------------------------------------------------------------
# submitting + claiming
# ---------------------------------------------------------------------------
def queue_approval_run(
    stack,
    definition: dict,
    *,
    owner=None,
    input_value: dict | None = None,
    name: str = "t087 approval node",
    csv: bool = False,
):
    """Create one real debug run of the given approval definition."""
    owner = owner or t033.golden_owner(stack)
    application_id = t027.application_with_definition(owner.creator, definition, name=name)
    artifact_id = t042.ready_csv(stack, owner) if csv else None
    run = t042.submit_linear_run(
        owner,
        application_id,
        artifact_id=artifact_id,
        input_value=input_value if input_value is not None else {},
    )
    return owner, application_id, run


def claim_next_run(
    stack,
    *,
    worker_id: str = "t087-supervisor",
    execution_unit_id: str = "t087-unit",
) -> dict:
    """Claim the one ready job through the real supervisor mTLS capacity."""
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id=worker_id, execution_unit_id=execution_unit_id
    )
    assert response.status == 200, response.text
    return response.json


def exec_once(stack):
    """Run one real supervisor attempt with the finalize worker alongside.

    ``ORCHARA_OBJECT_DIR`` is the same sealed-object root the API writes, which
    wires T083's real recovery seam: a resumed attempt re-reads completed node
    data artifacts (re-hashing them) instead of re-executing the node. This is the
    first real-stack scenario with a genuine wait, so the seam is exercised here.
    """
    return t083.run_once(
        stack, env_extra={"ORCHARA_OBJECT_DIR": str(stack.object_dir)}
    )


def wait_until(predicate, *, timeout: float = 30.0, interval: float = 0.05):
    """Poll a database-backed predicate (no fixed sleeps in the tests)."""
    import time

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition never became true: {last!r}")


# ---------------------------------------------------------------------------
# the node-approvals worker route
# ---------------------------------------------------------------------------
def prepare_body(
    lease: dict,
    *,
    node_id: str = "approve1",
    title: str = "Approve the order",
    expires_seconds: int = 3600,
    proposal: dict | None = None,
    attempt_token: str | None = None,
) -> dict:
    return {
        "attempt_token": lease["attempt_token"] if attempt_token is None else attempt_token,
        "node_id": node_id,
        "title": title,
        "expires_seconds": expires_seconds,
        "proposal": proposal
        if proposal is not None
        else {"subject": "order-42", "amount": "1250.00"},
    }


def prepare_node_approval(stack, lease: dict, *, body: dict | None = None, client=None):
    target = client or t041.worker_client(stack)
    return target.post(
        NODE_APPROVAL_PATH.format(run_id=lease["run_id"]),
        body if body is not None else prepare_body(lease),
    )


# ---------------------------------------------------------------------------
# decisions (contract path)
# ---------------------------------------------------------------------------
decide = t050.decide
get_approval = t050.get_approval
list_approvals = t050.list_approvals
point_expiry_past = t050.point_expiry_past
run_row = t050.run_row


# ---------------------------------------------------------------------------
# database + artifact reads
# ---------------------------------------------------------------------------
def approval_rows(dbname: str, *, run_id: str | None = None) -> list[dict]:
    where = "TRUE"
    if run_id:
        where = "run_id = " + st.quote_literal(run_id) + "::uuid"
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, run_id::text AS run_id, operation_id::text AS operation_id, "
        "node_key, proposal_artifact_id::text AS proposal_artifact_id, params_sha256, "
        "approver_id::text AS approver_id, state, expires_at::text AS expires_at, "
        "decided_at::text AS decided_at, decided_by::text AS decided_by, decision_key "
        "FROM platform.approvals "
        f"WHERE {where} ORDER BY node_key, params_sha256, id",
    )


def approval_row(dbname: str, approval_id: str) -> dict | None:
    rows = [row for row in approval_rows(dbname) if row["id"] == approval_id]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def output_artifact_id(dbname: str, run_id: str) -> str | None:
    return t033.output_artifact_id(dbname, run_id)


def node_result_rows(dbname: str, run_id: str) -> list[dict]:
    return t042.node_result_rows(dbname, run_id)


def node_states(dbname: str, run_id: str) -> dict[str, list[str]]:
    return t083.node_states(dbname, run_id)


def node_events(dbname: str, run_id: str) -> list[dict]:
    return t042.node_events(dbname, run_id)


def run_events(dbname: str, run_id: str, event_type: str) -> list[dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT seq, type, payload::text AS payload FROM platform.run_events "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
        f"AND type = {st.quote_literal(event_type)} ORDER BY seq",
    )
    for row in rows:
        row["payload"] = json.loads(row["payload"])
    return rows


def operation_rows(dbname: str, run_id: str) -> list[dict]:
    return t041.operation_rows(dbname, run_id)


def usage_ledger_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, entry_key FROM platform.usage_ledger "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY entry_key",
    )


def proposal_artifact_row(dbname: str, artifact_id: str) -> dict:
    return t041.artifact_row(dbname, artifact_id)


def proposal_document(stack, approval: dict) -> dict:
    """Read the sealed server proposal object the human page renders."""
    artifact = proposal_artifact_row(stack.dbname, approval["proposal_artifact_id"])
    payload = (stack.object_dir / artifact["object_key"]).read_bytes()
    return json.loads(payload)


def artifact_document(stack, owner, artifact_id: str) -> dict:
    content = t033.artifact_content(owner.creator, artifact_id)
    assert content.status == 200, content.text
    return json.loads(content.raw)


def run_artifact_ids(dbname: str, run_id: str) -> set[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT artifact_id::text AS artifact_id FROM platform.run_artifacts "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid",
    )
    return {row["artifact_id"] for row in rows}


def approval_for_node(dbname: str, run_id: str, node_id: str) -> dict | None:
    rows = [row for row in approval_rows(dbname, run_id=run_id)
            if row["node_key"] == "node:" + node_id]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# test-only fault injection for the atomic expiry hand-off
# ---------------------------------------------------------------------------
def install_waiting_decision_fault(dbname: str) -> None:
    """Make the waiting-decision UPDATE fail until the fault is cleared.

    The trigger raises inside the expiry transaction, which is exactly the
    window the T087 P1 finding describes: the approval must not stay `expired`
    while the Run is still parked with an empty waiting decision.
    """
    st.sql(
        dbname,
        """
        CREATE OR REPLACE FUNCTION platform.t087_fail_waiting_decision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 't087 injected waiting handoff failure';
        END
        $$;
        DROP TRIGGER IF EXISTS t087_fail_waiting_decision ON platform.run_waitings;
        CREATE TRIGGER t087_fail_waiting_decision
        BEFORE UPDATE ON platform.run_waitings
        FOR EACH ROW EXECUTE FUNCTION platform.t087_fail_waiting_decision();
        """,
    )


def clear_waiting_decision_fault(dbname: str) -> None:
    st.sql(
        dbname,
        """
        DROP TRIGGER IF EXISTS t087_fail_waiting_decision ON platform.run_waitings;
        DROP FUNCTION IF EXISTS platform.t087_fail_waiting_decision();
        """,
    )


# ---------------------------------------------------------------------------
# no-future-write fixture (real write-risk connector + real capability proxy)
# ---------------------------------------------------------------------------
def bind_write_connector(stack, admin) -> dict:
    """Register one write-risk connector policy and freeze it into the tool."""
    policy = t048.register(
        stack,
        admin,
        origin="https://connector.example.test",
        method="POST",
        path_prefix="/v1",
        request_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "body": {"type": "object"},
            },
            "required": ["path", "body"],
            "additionalProperties": False,
        },
        response_schema={"type": "object"},
        risk_class="write",
    )
    t048.bind_tool_connectors(stack.dbname, [policy["id"]])
    return policy


def invoke_write(stack, lease: dict, *, policy_id: str, logical_key: str):
    return t050.invoke_write(
        stack,
        lease,
        policy_id=policy_id,
        params={"path": "/v1/orders", "body": {"amount": 1250, "currency": "CNY"}},
        logical_key=logical_key,
    )


def new_uuid() -> str:
    return str(uuid.uuid4())
