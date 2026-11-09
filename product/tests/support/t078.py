"""F07/T078 real-stack helpers: parent invocation keys, child Runs and results.

The stack is T029's (throwaway PostgreSQL migrated by the real ``cmd/admin`` chain,
the compiled ``cmd/api`` behind real HTTP with the signed-link object store and the
real internal mTLS listener) plus the T033 supervisor binary. Every assertion reads
the authoritative rows; the child Run is always created by the real ``spawnChild``
endpoint, never by test SQL.

Two version producers are used deliberately:

* :func:`create_child_version` seals the exact canonical manifest bytes through the
  real validator and stores the version row the F02B publisher stores (the T075
  fixture). Its docstring says which transaction is skipped.
* :func:`publish_version` drives the real ``submitApplication`` -> administrator
  approve transaction with a guard-accepted five-category report (T068's
  documented review fixture: real evaluation/case/run rows, terminal states
  seeded). Used by the T075 closure test, whose point is the publication
  transaction itself.
"""

from __future__ import annotations

import json
import time

from support import execution_support as execution
from support import f00b, t025, t027, t029, t033, t035, t068, t075
from support import stack as st

KIND_CHILDREN = "children"


# ---------------------------------------------------------------------------
# internal clients
# ---------------------------------------------------------------------------
class ChildrenInternalClient(t029.ExecutionInternalClient):
    """T029's execution client plus the contract spawnChild and the child result."""

    def spawn_child(
        self,
        run_id: str,
        *,
        attempt_token: str,
        logical_key: str,
        application_version_id: str,
        input: dict | None = None,
        body: dict | None = None,
        **kwargs,
    ):
        if body is None:
            body = {
                "attempt_token": attempt_token,
                "logical_key": logical_key,
                "application_version_id": application_version_id,
                "input": input if input is not None else {},
            }
        return self.post(f"/internal/v1/runs/{run_id}/children", body, **kwargs)

    def child_result(
        self,
        run_id: str,
        child_run_id: str,
        *,
        attempt_token: str,
        body: dict | None = None,
        **kwargs,
    ):
        if body is None:
            body = {"attempt_token": attempt_token}
        return self.post(
            f"/internal/v1/runs/{run_id}/children/{child_run_id}/result", body, **kwargs
        )


def children_client(stack, role: str = "worker") -> ChildrenInternalClient:
    return ChildrenInternalClient(
        stack.certificates.internal_url,
        stack.certificates.ca.cert,
        stack.certificates.client(role),
    )


def waiting_client(stack, role: str = "supervisor") -> t035.WaitingInternalClient:
    return t035.execution_client(stack, role)


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------
def child_definition() -> dict:
    """A start -> end child: validates its sealed input and echoes one field.

    This is a real executable graph for the Go executor (no python/sandbox), so a
    child Run is executed by the real supervisor binary, not by test SQL.
    """
    return {
        "dsl_version": "1.0",
        "input_schema": {
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "required": ["source"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"echo": {"type": "string"}},
            "required": ["echo"],
            "additionalProperties": False,
        },
        "nodes": [
            {"id": "start", "type": "start", "inputs": {}, "output_schema": {"type": "object"}, "config": {}},
            {
                "id": "end",
                "type": "end",
                "inputs": {"echo": {"ref": "input#/source"}},
                "output_schema": {"type": "object"},
                "config": {},
            },
        ],
        "edges": [{"source": "start", "target": "end"}],
        "policy": dict(t075.POLICY),
    }


def agent_child_definition() -> dict:
    """A start -> end child with an empty input object and a fixed output.

    The dev-stub model auto-calls the first declared tool with ``{}`` arguments, so
    this child accepts ``{}`` and returns a constant; it exists to prove the
    agent's ``subapp_versions`` tool call becomes a platform child Run.
    """
    return {
        "dsl_version": "1.0",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"echo": {"type": "string"}},
            "required": ["echo"],
            "additionalProperties": False,
        },
        "nodes": [
            {"id": "start", "type": "start", "inputs": {}, "output_schema": {"type": "object"}, "config": {}},
            {
                "id": "end",
                "type": "end",
                "inputs": {"echo": {"literal": "agent-child"}},
                "output_schema": {"type": "object"},
                "config": {},
            },
        ],
        "edges": [{"source": "start", "target": "end"}],
        "policy": dict(t075.POLICY),
    }


def agent_parent_definition(model_version_id: str, child_version_id: str) -> dict:
    """start -> agent -> end: the agent may call only the sealed child version.

    No python node is needed: an agent node validates and runs its own inputs. The
    dev-stub provider plays a model that calls the first declared tool
    (``call_app_1``) once, with an empty argument object.
    """
    return {
        "dsl_version": "1.0",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        "output_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "nodes": [
            {"id": "start", "type": "start", "inputs": {}, "output_schema": {"type": "object"}, "config": {}},
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
                    "instructions": "Call the fixed child application once, then answer.",
                    "max_turns": 3,
                    "tool_versions": [],
                    "subapp_versions": [child_version_id],
                },
            },
            {
                "id": "end",
                "type": "end",
                "inputs": {"text": {"ref": "node:analysis#/text"}},
                "output_schema": {"type": "object"},
                "config": {},
            },
        ],
        "edges": [
            {"source": "start", "target": "analysis"},
            {"source": "analysis", "target": "end"},
        ],
        "policy": dict(t075.POLICY),
    }


def parent_definition(
    child_version_ids: list[str],
    *,
    input_fields: tuple[str, ...] = ("source",),
    output_key: str = "echo",
    max_child_calls: int = 12,
    max_child_depth: int = 3,
) -> dict:
    """start -> call_app(version) -> end with an explicit minimal mapping.

    Two nodes are allowed for the invocation-key conflict case: the parent Run is
    claimed directly (never executed), so the executor's one-call_app chain rule
    does not matter there; publication/validate accepts any declared count within
    ``max_child_calls``.
    """
    nodes = []
    for index, version in enumerate(child_version_ids):
        node_id = "child" if index == 0 else f"child{index}"
        nodes.append(
            {
                "id": node_id,
                "type": "call_app",
                "inputs": {field: {"ref": f"input#/{field}"} for field in input_fields},
                "output_schema": {
                    "type": "object",
                    "properties": {output_key: {"type": "string"}},
                    "required": [output_key],
                },
                "config": {"application_version": version},
            }
        )
    definition = t075.chain_definition(
        nodes,
        input_schema={
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "required": ["source"],
            "additionalProperties": True,
        },
        output_schema={
            "type": "object",
            "properties": {output_key: {"type": "string"}},
            "required": [output_key],
        },
        policy={
            "budget_limit": "1.00",
            "currency": "USD",
            "max_active_seconds": 300,
            "max_child_calls": max_child_calls,
            "max_child_depth": max_child_depth,
        },
    )
    # The last node of chain_definition is the end node; map the first child's
    # result into the definition output.
    definition["nodes"][-1]["inputs"] = {
        output_key: {"ref": "node:child#" + "/" + output_key}
    }
    return definition


# ---------------------------------------------------------------------------
# version producers
# ---------------------------------------------------------------------------
def create_child_version(stack, session, *, version_no: int = 1, definition: dict | None = None) -> dict:
    """Seal one child version in the F02B publisher's stored shape (T075 fixture).

    Real ``POST /validate`` -> exact canonical manifest bytes -> version row ->
    version_dependencies edges. The publication review transaction is the one
    skipped step; the T075 closure test uses :func:`publish_version` instead.
    """
    application = t025.create_application_or_fail(session, "t078 child")
    saved = t075.save_definition(
        session, application["id"], definition if definition is not None else child_definition()
    )
    assert saved.status == 200, saved.text
    sealed = t075.seal_version(stack, session, application["id"], version_no=version_no)
    sealed["application_id"] = application["id"]
    sealed["published_by"] = "publisher-shape-fixture"
    return sealed


def publish_version(stack, session, application_id: str) -> str:
    """Publish the current draft through the real review transaction.

    The five-category report is T068's guard-accepted fixture: the evaluation,
    case runs and snapshot are real rows, the per-case terminal states are seeded
    (the T068 report documents this for its review-transaction fixtures). The
    submit and approve endpoints, the frozen manifest, the version and the
    ``version_dependencies`` edges are all real.
    """
    report = t068.seeded_passed_report(
        stack, session=session, application_id=application_id
    )
    snapshot = t027.snapshot_row(stack.dbname, report["evaluation"]["snapshot_id"])
    submitted = t068.submit_publication(
        session,
        application_id,
        expected_manifest_sha256=snapshot["manifest_sha256"],
        evaluation_report_id=report["report"]["id"],
    )
    assert submitted.status == 202, submitted.text
    admin = f00b.admin_session(stack)
    decided = t068.decided_review(
        admin,
        submitted.json["id"],
        decision="approve",
        manifest_sha256=snapshot["manifest_sha256"],
        reason="T078 closure: five-category report reviewed",
    )
    assert decided.status == 200, decided.text
    return decided.json["version_id"]


def publish_application(stack, session, name: str, definition: dict) -> tuple[str, str]:
    """Create an application, save ``definition``, publish it and return ids."""
    application = t025.create_application_or_fail(session, name)
    saved = t075.save_definition(session, application["id"], definition)
    assert saved.status == 200, saved.text
    version_id = publish_version(stack, session, application["id"])
    return application["id"], version_id


# ---------------------------------------------------------------------------
# parent runs
# ---------------------------------------------------------------------------
def debug_parent(stack, session, definition: dict, input_value: dict) -> tuple[str, dict]:
    """Create one real debug parent Run from a call_app definition."""
    application_id = t027.application_with_definition(session, definition)
    revision = t027.draft_revision(session, application_id)
    response = t027.submit_debug(
        session, application_id, draft_revision=revision, input_value=input_value
    )
    assert response.status == 202, response.text
    return application_id, response.json


def claim_run(stack, *, run_id: str | None = None, worker_id: str = "t078-supervisor") -> dict:
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id=worker_id, execution_unit_id="t078-unit"
    )
    assert response.status == 200, response.text
    lease = response.json
    if run_id is not None:
        assert lease["run_id"] == run_id, (lease, run_id)
    return lease


def spawn(
    stack,
    parent_run_id: str,
    *,
    attempt_token: str,
    logical_key: str,
    application_version_id: str,
    input: dict,
    role: str = "worker",
):
    return children_client(stack, role).spawn_child(
        parent_run_id,
        attempt_token=attempt_token,
        logical_key=logical_key,
        application_version_id=application_version_id,
        input=input,
    )


# ---------------------------------------------------------------------------
# supervisor execution
# ---------------------------------------------------------------------------
def execute_once(stack, **env_kwargs) -> tuple[dict, object]:
    """Run one real ``cmd/supervisor exec --once`` and return its JSON report.

    A start -> end or call_app chain uses no sandbox, so the sandbox variables are
    still supplied (the process validates its configuration) but never used.
    """
    env = execution.supervisor_exec_env(stack, **env_kwargs)
    # The uploads the executor registers must be activated by the real
    # artifact_finalize pool, exactly like the T033 tests.
    with execution.FinalizeWorker(stack):
        completed = execution.run_supervisor_exec(stack, env, timeout=120)
    report = execution.exec_report(completed)
    return report, completed


def resolve_children_wait(stack, parent_run_id: str, child_run_id: str, *, outcome: str = "succeeded"):
    """The documented T035 bridge: F07/T081 owns the automatic terminal hook."""
    return waiting_client(stack).resolve_waiting(
        parent_run_id, kind=KIND_CHILDREN, outcome=outcome, child_run_id=child_run_id
    )


# ---------------------------------------------------------------------------
# database read models
# ---------------------------------------------------------------------------
def run_children_rows(dbname: str, parent_run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT child_run_id::text AS child_run_id, parent_run_id::text AS parent_run_id, "
        "parent_attempt_token::text AS parent_attempt_token, parent_invocation_key, "
        "child_version_id::text AS child_version_id, input_sha256, "
        "input_artifact_id::text AS input_artifact_id, io_mapping::text AS io_mapping, "
        "input_schema_sha256, output_schema_sha256, required, depth, "
        "created_at::text AS created_at "
        "FROM platform.run_children "
        f"WHERE parent_run_id = {st.quote_literal(parent_run_id)}::uuid "
        "ORDER BY created_at, child_run_id",
    )


def run_children_row(dbname: str, parent_run_id: str, logical_key: str) -> dict | None:
    rows = [
        row
        for row in run_children_rows(dbname, parent_run_id)
        if row["parent_invocation_key"] == logical_key
    ]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def run_row(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, state, phase, revision, kind, "
        "parent_run_id::text AS parent_run_id, parent_invocation_key, "
        "owner_id::text AS owner_id, application_version_id::text AS application_version_id, "
        "execution_snapshot_id::text AS execution_snapshot_id, "
        "checkpoint_thread_id::text AS checkpoint_thread_id, "
        "input_artifact_id::text AS input_artifact_id, "
        "output_artifact_id::text AS output_artifact_id, error_code, "
        "conversation_id::text AS conversation_id, "
        "budget_limit::text AS budget_limit, currency, max_active_ms, "
        "cancel_requested_at::text AS cancel_requested_at, "
        "created_at::text AS created_at, finished_at::text AS finished_at "
        "FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def run_count(dbname: str) -> int:
    return int(st.sql(dbname, "SELECT count(*) FROM platform.runs"))


def child_run_ids(dbname: str, parent_run_id: str) -> list[str]:
    return [row["child_run_id"] for row in run_children_rows(dbname, parent_run_id)]


def child_count(dbname: str, parent_run_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.runs WHERE parent_run_id = "
            f"{st.quote_literal(parent_run_id)}::uuid",
        )
    )


def attempt_rows(dbname: str, run_id: str) -> list[dict]:
    return t029.attempts_for_run(dbname, run_id)


def artifact_bytes(stack, artifact_id: str) -> bytes:
    return t075.artifact_bytes(stack, artifact_id)


def waiting_row(dbname: str, run_id: str) -> dict | None:
    return t035.waiting_row(dbname, run_id)


def wait_run_state(dbname: str, run_id: str, states: set[str], *, timeout: float = 60.0) -> dict:
    return t033.wait_run_state(dbname, run_id, states, timeout=timeout)


def wait_for_resume_job(dbname: str, run_id: str, *, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        last = waiting_row(dbname, run_id)
        if last and last.get("resume_job_id"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"no resume job appeared for {run_id}: {last}")


def job_for_run(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, state, current_attempt_token::text AS current_attempt_token "
        "FROM platform.jobs "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY created_at, id",
    )


__all__ = [name for name in dir() if not name.startswith("_")]
