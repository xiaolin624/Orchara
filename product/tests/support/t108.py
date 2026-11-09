"""F13B/T108 real-stack helpers: the three delegation faults and their evidence.

The stack is T029's (a throwaway PostgreSQL 16.4 database migrated by the real
``cmd/admin`` chain, the compiled ``cmd/api`` behind real HTTP with the real mTLS
internal listener and object store) plus the T033 supervisor binary and the T081
child-pool helpers. Nothing is mocked:

* FAULT-07 issues two **barrier-synchronised** contract ``spawnChild`` calls from
  one claimed parent attempt (same pinned child ``application_version``, distinct
  logical keys), executes both children with two concurrent real
  ``cmd/supervisor exec --once`` OS processes, and then resumes the same parent
  attempt so the real graph scheduler replays both invocations to one join.
* FAULT-08 builds 20 real parent Runs that spawn one child each and park in
  ``waiting_children``, saturates the Python execution pool, shrinks the real
  child pool, and samples the authoritative rows while the children progress.
* FAULT-13 drives one nested condition/join graph with a skipped branch and one
  required branch that fails (the child's real ``budget_exhausted``), and reads
  the durable join facts / event sequence.

The evidence contract (the same shape T106's ``fault_support`` uses): each fault
records its injection point, the input/seed and an independently recomputed hash,
the expected pre-registered outcome versus the observed one, the independent truth
(PostgreSQL rows, process reports, sealed artifact bytes), the commands that
produced it, and ``not_run``. The JSON is written only when
``F13B_WRITE_EVIDENCE=1`` so an ordinary pytest run never rewrites a committed
evidence file; every persisted string passes through ``tools/evidence_redaction``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
TOOLS_ROOT = PRODUCT_ROOT / "tools"
for _entry in (str(TOOLS_ROOT), str(TESTS_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from evidence_redaction import find_violations, redact_value  # noqa: E402
from support import run_observations as observations
from support import execution_support as execution
from support import stack as st  # noqa: E402
from support import t029, t033, t078, t081  # noqa: E402

#: Mirrors the T101/T106 evidence convention: only an explicit evidence run writes.
WRITE_EVIDENCE_ENV = "F13B_WRITE_EVIDENCE"
EVIDENCE_JSON = PRODUCT_ROOT / "reports" / "f13b" / "T108-faults.json"

UNIT = "unit-t108"

ENVIRONMENT = {
    "profile": "dev-stub (ORCHARA_PROFILE=dev-stub; no vendor credentials or endpoint)",
    "postgres": "PostgreSQL 16.4 at 127.0.0.1:55432; one throwaway database per stack",
    "api": "compiled cmd/api: real mTLS internal listener, real object store, HMAC signed links",
    "supervisor": "compiled cmd/supervisor exec --once as a real one-shot OS process",
    "maintenance": "compiled cmd/maintenance artifact_finalize worker beside each supervisor run",
    "sandbox": (
        "WSL2/runsc is available on this host but these faults execute no python node: "
        "the delegated children are start->end and start->agent->end graphs"
    ),
    "not_run": [
        "a real vendor model (dev-stub profile; no credentials/endpoint on this host)",
        "a real browser DOM",
        "a resident supervisor daemon (the production one-shot `exec --once` shape is used)",
        "20 parents each with simultaneously in-flight children (the child pool is deliberately "
        "shrunk to two slots, so at most two child attempts execute at once)",
    ],
}


# ---------------------------------------------------------------------------
# independent hashing / sealed-byte reads
# ---------------------------------------------------------------------------
def canonical_digest(value) -> tuple[bytes, str]:
    """Canonical JSON bytes and sha256, recomputed in Python from the input."""
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return canonical, hashlib.sha256(canonical).hexdigest()


def definition_digest(definition: dict) -> dict:
    canonical, digest = canonical_digest(definition)
    return {"sha256": digest, "size_bytes": len(canonical), "document": definition}


def artifact_bytes(stack, artifact_id: str) -> bytes:
    """The sealed object bytes, verified against the artifact row's sha256."""
    return t078.artifact_bytes(stack, artifact_id)


def artifact_document(stack, artifact_id: str) -> dict:
    return json.loads(artifact_bytes(stack, artifact_id))


# ---------------------------------------------------------------------------
# claims / children / parking (the real internal protocol only)
# ---------------------------------------------------------------------------
def claim(stack, *, run_id: str | None = None, unit: str = UNIT) -> dict:
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t108-supervisor", execution_unit_id=unit
    )
    assert response.status == 200, response.text
    lease = response.json
    if run_id is not None:
        assert lease["run_id"] == run_id, (lease, run_id)
    lease.setdefault("_execution_unit_id", unit)
    return lease


def spawn(
    stack,
    parent_run_id: str,
    *,
    attempt_token: str,
    logical_key: str,
    application_version_id: str,
    input_value: dict,
):
    return t078.spawn(
        stack,
        parent_run_id,
        attempt_token=attempt_token,
        logical_key=logical_key,
        application_version_id=application_version_id,
        input=input_value,
    )


def claim_child(stack, *, expected_child_id: str | None = None, unit: str = UNIT) -> dict:
    """One real claim that must hand back a delegated child Run."""
    lease = claim(stack, unit=unit)
    if expected_child_id is not None:
        assert lease["run_id"] == expected_child_id, (lease, expected_child_id)
    return lease


def spawn_concurrently(
    stack,
    parent_run_id: str,
    *,
    attempt_token: str,
    application_version_id: str,
    calls: list[tuple[str, dict]],
) -> list[dict]:
    """Fire every spawnChild at the same instant behind a threading barrier.

    Each call is an independent real HTTP connection over the mTLS listener. The
    barrier makes the *submission* simultaneity explicit (the ticket's injection
    point), not a wall-clock sleep.
    """
    barrier = threading.Barrier(len(calls))
    results: list[dict] = [{} for _ in calls]

    def fire(index: int, logical_key: str, payload: dict) -> None:
        barrier.wait(timeout=60)
        started = time.perf_counter()
        response = spawn(
            stack,
            parent_run_id,
            attempt_token=attempt_token,
            logical_key=logical_key,
            application_version_id=application_version_id,
            input_value=payload,
        )
        finished = time.perf_counter()
        results[index] = {
            "logical_key": logical_key,
            "input": payload,
            "status": response.status,
            "body": response.json,
            "text": response.text,
            "started_at": started,
            "finished_at": finished,
        }

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [
            pool.submit(fire, index, logical_key, payload)
            for index, (logical_key, payload) in enumerate(calls)
        ]
        for future in futures:
            future.result(timeout=120)
    ordered = list(results)
    ordered.sort(key=lambda item: item["started_at"])
    return ordered


def park_waiting(stack, lease: dict, *, child_run_id: str) -> None:
    t081.park_waiting(stack, lease, child_run_id=child_run_id)


def execute_once(stack, **env_kwargs):
    return t081.execute_once(stack, **env_kwargs)


def execute_once_with_slot_peak(stack, *, timeout: float = 300.0) -> tuple[dict, dict]:
    """Run one real supervisor process and sample the live child-slot counter.

    The counter is read from PostgreSQL while the process runs (each read is a
    real query). The peak is observation only; the durable attempt rows are the
    authoritative simultaneous-liveness proof (see :func:`child_attempt_overlap`).
    """
    execution.rootfs_digest()  # pre-warm the cached sandbox digest before threading
    box: dict = {}
    error: list[str] = []

    def run() -> None:
        try:
            box["report"] = execute_once(stack)[0]
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            error.append(f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=run)
    thread.start()
    peak = 0
    reads: list[int] = []
    deadline = time.monotonic() + timeout
    while thread.is_alive():
        if time.monotonic() > deadline:
            raise AssertionError("the supervisor process did not finish")
        counter = t081.child_counter(stack.dbname)
        used = int(counter["used"]) if counter else 0
        reads.append(used)
        peak = max(peak, used)
        time.sleep(0.005)
    thread.join(timeout=120)
    assert not error, error
    assert "report" in box
    return box["report"], {"peak_child_slots_used": peak, "reads": reads}


def child_attempt_overlap(dbname: str, held_child: str, executing_child: str) -> dict:
    """Durable facts proving both child attempts were live at the same instant."""
    rows = st.sql_rows(
        dbname,
        "SELECT "
        " (SELECT count(*) FROM platform.run_attempts a WHERE a.run_id = "
        + st.quote_literal(held_child)
        + "::uuid AND a.exited_at IS NULL) AS held_live_attempts, "
        " (SELECT min(a.started_at) FROM platform.run_attempts a WHERE a.run_id = "
        + st.quote_literal(held_child)
        + "::uuid) AS held_started_at, "
        " (SELECT count(*) FROM platform.run_attempts a WHERE a.run_id = "
        + st.quote_literal(executing_child)
        + "::uuid AND a.exited_at IS NOT NULL) AS executing_exited_attempts, "
        " (SELECT min(a.started_at) FROM platform.run_attempts a WHERE a.run_id = "
        + st.quote_literal(executing_child)
        + "::uuid) AS executing_started_at, "
        " (SELECT min(a.exited_at) FROM platform.run_attempts a WHERE a.run_id = "
        + st.quote_literal(executing_child)
        + "::uuid) AS executing_exited_at, "
        " (SELECT "
        "    (SELECT min(a.started_at) FROM platform.run_attempts a WHERE a.run_id = "
        + st.quote_literal(held_child)
        + "::uuid)"
        "    <= "
        "    (SELECT min(b.started_at) FROM platform.run_attempts b WHERE b.run_id = "
        + st.quote_literal(executing_child)
        + "::uuid)"
        "  ) AS held_started_before_executing, "
        " (SELECT "
        "    (SELECT min(b.started_at) FROM platform.run_attempts b WHERE b.run_id = "
        + st.quote_literal(executing_child)
        + "::uuid)"
        "    <= "
        "    (SELECT min(b.exited_at) FROM platform.run_attempts b WHERE b.run_id = "
        + st.quote_literal(executing_child)
        + "::uuid)"
        "  ) AS executing_interval_ordered",
    )
    assert len(rows) == 1, rows
    return rows[0]


def attempt_exit_evidence(dbname: str, run_id: str) -> list[dict]:
    """Every attempt's exit evidence for one Run, oldest first."""
    from support import t029

    evidence = []
    for attempt in t029.attempts_for_run(dbname, run_id):
        row = t029.attempt_row(dbname, attempt["token"])
        evidence.append(
            {
                "token_sha256": hashlib.sha256(attempt["token"].encode()).hexdigest(),
                "job_id": row["job_id"],
                "started_at": row["started_at"],
                "exited_at": row["exited_at"],
                "exit_evidence": json.loads(row["exit_evidence"])
                if row["exit_evidence"]
                else None,
            }
        )
    return evidence


def complete_child(stack, lease: dict, *, output: bytes) -> None:
    """Drive one claimed child to a real succeeded terminal through the protocol.

    This is the T081/T078 documented seam for a child that the test (not a
    supervisor process) owns: createWorkerUpload -> complete -> finalize ->
    register, submitWorkerResult, attempt-exit. The platform still validates the
    bytes against the child's frozen output schema and commits the state.
    """
    artifacts = t033.artifact_client(stack, "worker")
    artifact_id, _sha = t033.upload_run_artifact(
        stack,
        artifacts,
        lease,
        content=output,
        purpose="final_output",
        media_type="application/json",
        relation="output",
    )
    supervisor = t029.execution_client(stack, "supervisor")
    proposed = supervisor.submit_result(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="succeeded",
        output_artifact_id=artifact_id,
    )
    assert proposed.status == 200, proposed.text
    # The executor always certifies the *attempt* with started=true and records
    # ``sandbox_started=false`` separately (presetexec.go: "started=true certifies
    # the attempt; sandbox_started=false records that no unit ran"). A
    # started=false exit is the platform's "never ran" release path and would
    # requeue the job instead of committing the proposal.
    exited = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id=lease.get("_execution_unit_id", UNIT),
        evidence=t029.exit_evidence(lease, started=True, sandbox_started=False),
    )
    assert exited.status == 200, exited.text


def child_output_from_sealed_input(stack, parent_run_id: str, logical_key: str) -> bytes:
    """The echo child's output for the input the platform sealed for that call."""
    receipt = t078.run_children_row(stack.dbname, parent_run_id, logical_key)
    assert receipt is not None, (parent_run_id, logical_key)
    sealed = artifact_document(stack, receipt["input_artifact_id"])
    return json.dumps({"echo": sealed["source"]}, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# 20-parent capacity sampling (FAULT-08)
# ---------------------------------------------------------------------------
def delegation_snapshot(
    dbname: str, *, parent_ids: list[str], child_ids: list[str]
) -> dict:
    """One read of every authoritative delegation fact (a single SQL round trip).

    ``active`` means a child Run in ``running`` with exactly one live attempt and
    two live child-slot allocations (global + parent). A ``queued`` child has no
    attempt at all: queueing is never counted as execution.
    """
    child_list = ", ".join(st.quote_literal(child_id) + "::uuid" for child_id in child_ids)
    parent_list = ", ".join(st.quote_literal(parent_id) + "::uuid" for parent_id in parent_ids)
    rows = st.sql_rows(
        dbname,
        "WITH child_rows AS ("
        "  SELECT r.id::text AS id, r.state, "
        "    (SELECT count(*) FROM platform.run_attempts a WHERE a.run_id = r.id) AS attempts, "
        "    (SELECT count(*) FROM platform.run_attempts a "
        "      WHERE a.run_id = r.id AND a.exited_at IS NULL AND a.revoked_at IS NULL) AS live_attempts, "
        "    (SELECT count(*) FROM platform.quota_allocations q "
        "      WHERE q.dimension = 'child_slots' AND q.released_at IS NULL "
        "        AND q.holder_key IN (SELECT 'job:'||a.job_id FROM platform.run_attempts a "
        "          WHERE a.run_id = r.id AND a.exited_at IS NULL AND a.revoked_at IS NULL)) AS live_allocations "
        "  FROM platform.runs r WHERE r.id IN (" + child_list + ")), "
        "parent_rows AS ("
        "  SELECT r.id::text AS id, r.state, (w.run_id IS NOT NULL) AS has_waiting "
        "  FROM platform.runs r LEFT JOIN platform.run_waitings w ON w.run_id = r.id "
        "  WHERE r.id IN (" + parent_list + ")) "
        "SELECT "
        " (SELECT coalesce(json_agg(child_rows), '[]'::json) FROM child_rows) AS children, "
        " (SELECT coalesce(json_agg(parent_rows), '[]'::json) FROM parent_rows) AS parents, "
        " (SELECT coalesce(json_agg(c), '[]'::json) FROM "
        "   (SELECT scope, dimension, used, capacity FROM platform.quota_counters) c) AS counters, "
        " (SELECT count(*) FROM platform.jobs WHERE state = 'ready') AS claimable, "
        " (SELECT count(*) FROM platform.quota_allocations "
        "   WHERE dimension = 'child_slots' AND released_at IS NULL) AS live_child_allocations",
    )
    assert len(rows) == 1, rows
    payload = rows[0]
    children_rows = payload["children"] or []
    parent_rows = payload["parents"] or []
    counters = {
        (row["scope"], row["dimension"]): row for row in (payload["counters"] or [])
    }

    states: dict[str, int] = {}
    children: dict[str, dict] = {}
    queued_with_attempt: list[str] = []
    active_without_live_attempt: list[str] = []
    active_without_two_allocations: list[str] = []
    for row in children_rows:
        child_id = row["id"]
        live_allocations = int(row["live_allocations"])
        children[child_id] = {
            "state": row["state"],
            "attempts": int(row["attempts"]),
            "live_attempts": int(row["live_attempts"]),
            "live_child_allocations": live_allocations,
        }
        states[row["state"]] = states.get(row["state"], 0) + 1
        if row["state"] == "queued" and int(row["attempts"]):
            queued_with_attempt.append(child_id)
        if row["state"] == "running":
            if int(row["live_attempts"]) == 0:
                active_without_live_attempt.append(child_id)
            if live_allocations != 2:
                active_without_two_allocations.append(child_id)

    parent_states: dict[str, int] = {}
    waiting_parents = 0
    for row in parent_rows:
        parent_states[row["state"]] = parent_states.get(row["state"], 0) + 1
        if row["has_waiting"]:
            waiting_parents += 1

    child_pool = counters.get((t081.CHILD_SCOPE, t081.CHILD_DIMENSION))
    execution_pool = counters.get((t029.QUOTA_SCOPE, t029.QUOTA_DIMENSION))
    assert execution_pool is not None, "the 0016 execution counter is missing"
    return {
        "at_epoch_s": round(time.time(), 3),
        "children": children,
        "children_by_state": states,
        "active": states.get("running", 0),
        "queued": states.get("queued", 0),
        "completed": states.get("succeeded", 0),
        "failed": states.get("failed", 0),
        "queued_with_attempts": queued_with_attempt,
        "active_without_live_attempt": active_without_live_attempt,
        "active_without_two_allocations": active_without_two_allocations,
        "parents_by_state": parent_states,
        "parents_waiting_row": waiting_parents,
        "queued_parents": parent_states.get("queued", 0),
        "child_pool": dict(child_pool or {}),
        "execution_pool": dict(execution_pool),
        "live_child_allocations": int(payload["live_child_allocations"]),
        "claimable_jobs": int(payload["claimable"]),
    }


def children_final_rows(dbname: str, child_ids: list[str]) -> dict[str, dict]:
    """One query: each child's terminal row, sealed output and attempt count."""
    ids = ", ".join(st.quote_literal(child_id) + "::uuid" for child_id in child_ids)
    rows = st.sql_rows(
        dbname,
        "SELECT r.id::text AS id, r.state, r.output_artifact_id::text AS output_artifact_id, "
        " (SELECT count(*) FROM platform.run_attempts a WHERE a.run_id = r.id) AS attempts, "
        " r.error_code "
        "FROM platform.runs r WHERE r.id IN (" + ids + ")",
    )
    return {row["id"]: row for row in rows}


def parents_final_rows(dbname: str, parent_ids: list[str]) -> dict[str, dict]:
    """One query: each parent's state, one children decision and its resume job."""
    ids = ", ".join(st.quote_literal(parent_id) + "::uuid" for parent_id in parent_ids)
    rows = st.sql_rows(
        dbname,
        "SELECT r.id::text AS id, r.state, "
        " (SELECT count(*) FROM platform.run_waitings w WHERE w.run_id = r.id) AS waitings, "
        " (SELECT w.decision::text FROM platform.run_waitings w WHERE w.run_id = r.id) AS decision, "
        " (SELECT count(*) FROM platform.jobs j WHERE j.run_id = r.id AND j.kind = 'resume') AS resume_jobs, "
        " (SELECT min(j.state) FROM platform.jobs j WHERE j.run_id = r.id AND j.kind = 'resume') AS resume_job_state "
        "FROM platform.runs r WHERE r.id IN (" + ids + ")",
    )
    return {row["id"]: row for row in rows}


def assert_snapshot_invariants(snapshot: dict, *, total_children: int, capacity: int) -> None:
    """The FAULT-08 pre-registered invariant: queue is not execution."""
    assert snapshot["active"] + snapshot["queued"] + snapshot["completed"] + snapshot[
        "failed"
    ] == total_children, snapshot
    assert snapshot["active"] <= capacity, snapshot
    assert snapshot["queued_with_attempts"] == [], snapshot
    assert snapshot["active_without_live_attempt"] == [], snapshot
    assert snapshot["active_without_two_allocations"] == [], snapshot
    assert snapshot["live_child_allocations"] == 2 * snapshot["active"], snapshot
    pool = snapshot["child_pool"]
    assert int(pool["used"]) == snapshot["active"], (pool, snapshot["active"])
    assert int(pool["used"]) <= int(pool["capacity"]), pool


def latency_percentiles(dbname: str, child_ids: list[str]) -> dict:
    """P50/P95 arrival-to-completion and queue delay from the authoritative rows."""
    ids = ", ".join(st.quote_literal(child_id) + "::uuid" for child_id in child_ids)
    rows = st.sql_rows(
        dbname,
        "WITH facts AS ("
        "  SELECT r.id, r.created_at, r.finished_at, r.started_at, "
        "         (SELECT min(a.started_at) FROM platform.run_attempts AS a "
        "           WHERE a.run_id = r.id) AS first_attempt_at "
        "  FROM platform.runs AS r WHERE r.id IN (" + ids + ")) "
        "SELECT "
        " percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (finished_at - created_at))) AS p50_total_s, "
        " percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM (finished_at - created_at))) AS p95_total_s, "
        " percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (first_attempt_at - created_at))) AS p50_queue_s, "
        " percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM (first_attempt_at - created_at))) AS p95_queue_s, "
        " count(*) AS samples, count(finished_at) AS finished "
        "FROM facts",
    )
    assert len(rows) == 1, rows
    row = rows[0]
    return {
        "p50_total_s": float(row["p50_total_s"]),
        "p95_total_s": float(row["p95_total_s"]),
        "p50_queue_s": float(row["p50_queue_s"]),
        "p95_queue_s": float(row["p95_queue_s"]),
        "samples": int(row["samples"]),
        "finished": int(row["finished"]),
    }


# ---------------------------------------------------------------------------
# nested condition/join graph for FAULT-13
# ---------------------------------------------------------------------------
RESULTS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "object"}},
    "required": ["results"],
    "additionalProperties": False,
}


def _start_node() -> dict:
    return {
        "id": "start",
        "type": "start",
        "inputs": {},
        "output_schema": {"type": "object"},
        "config": {},
    }


def _end_node() -> dict:
    return {
        "id": "end",
        "type": "end",
        "inputs": {"results": {"ref": "node:join#/results"}},
        "output_schema": {"type": "object"},
        "config": {},
    }


def _condition(node_id: str, *, join_id: str, input_name: str, cases: list, default: str) -> dict:
    return {
        "id": node_id,
        "type": "condition",
        "inputs": {input_name: {"ref": f"input#/{input_name}"}},
        "config": {"join_id": join_id, "default_port": default, "cases": cases},
        "output_schema": {"type": "object"},
    }


def _join(node_id: str, *, fork_id: str, required: tuple[str, ...]) -> dict:
    return {
        "id": node_id,
        "type": "join",
        "inputs": {},
        "config": {
            "fork_id": fork_id,
            "mode": "all_active",
            "required_sources": list(required),
            "optional_sources": [],
        },
        "output_schema": dict(RESULTS_OUTPUT_SCHEMA),
    }


def _call_app(
    node_id: str,
    version_id: str,
    *,
    output_key: str,
    literal: str | None,
) -> dict:
    inputs = {} if literal is None else {"source": {"literal": literal}}
    return {
        "id": node_id,
        "type": "call_app",
        "inputs": inputs,
        "output_schema": {
            "type": "object",
            "properties": {output_key: {"type": "string"}},
            "required": [output_key],
            "additionalProperties": False,
        },
        "config": {"application_version": version_id},
    }


def nested_definition(
    child_version_id: str,
    *,
    output_key: str,
    literals: dict[str, str] | None = None,
) -> dict:
    """start -> choose --yes--> choose2 --yes--> deep / --no--> shallow -> join2
    --no--> direct -> join -> end.

    One condition region is nested inside another: ``choose2``/``join2`` are inside
    the ``choose``/``join`` region. Every branch is a real ``call_app`` node, so the
    executor spawns real child Runs and no sandbox is needed. The T075 mapping
    validation still runs over the sealed draft.
    """
    literals = literals or {}

    def literal(node_id: str) -> str | None:
        return literals.get(node_id)

    nodes = [
        _start_node(),
        _condition(
            "choose",
            join_id="join",
            input_name="mode",
            cases=[{"input": "mode", "op": "in", "value": ["nested", "deep"], "port": "yes"}],
            default="no",
        ),
        _condition(
            "choose2",
            join_id="join2",
            input_name="mode",
            cases=[{"input": "mode", "op": "eq", "value": "deep", "port": "yes"}],
            default="no",
        ),
        _call_app("deep", child_version_id, output_key=output_key, literal=literal("deep")),
        _call_app("shallow", child_version_id, output_key=output_key, literal=literal("shallow")),
        _join("join2", fork_id="choose2", required=("deep", "shallow")),
        _call_app("direct", child_version_id, output_key=output_key, literal=literal("direct")),
        _join("join", fork_id="choose", required=("join2", "direct")),
        _end_node(),
    ]
    edges = [
        {"source": "start", "target": "choose"},
        {"source": "choose", "target": "choose2", "port": "yes"},
        {"source": "choose", "target": "direct", "port": "no"},
        {"source": "choose2", "target": "deep", "port": "yes"},
        {"source": "choose2", "target": "shallow", "port": "no"},
        {"source": "deep", "target": "join2"},
        {"source": "shallow", "target": "join2"},
        {"source": "join2", "target": "join"},
        {"source": "direct", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return {
        "dsl_version": "1.0",
        "input_schema": {
            "type": "object",
            "properties": {"mode": {"type": "string"}},
            "required": ["mode"],
            "additionalProperties": False,
        },
        "output_schema": dict(RESULTS_OUTPUT_SCHEMA),
        "nodes": nodes,
        "edges": edges,
        "policy": {
            "budget_limit": "1.00",
            "currency": "USD",
            "max_active_seconds": 300,
            "max_child_calls": 12,
            "max_child_depth": 3,
        },
    }


# ---------------------------------------------------------------------------
# durable run facts shared by the faults
# ---------------------------------------------------------------------------
def node_status_events(dbname: str, run_id: str) -> list[dict]:
    # Imported lazily so the module keeps working if the F04B helper is absent.
    from support import t042

    return observations.node_events(dbname, run_id)


def event_seq_facts(dbname: str, run_id: str) -> dict:
    from support import t032

    rows = t032.run_events(dbname, run_id)
    seqs = [int(row["seq"]) for row in rows]
    return {
        "event_count": len(seqs),
        "first_seq": seqs[0] if seqs else None,
        "last_seq": seqs[-1] if seqs else None,
        "contiguous": seqs == list(range(1, len(seqs) + 1)),
        "seqs": seqs,
        "types": [row["type"] for row in rows],
    }


def join_firing(dbname: str, run_id: str, join_id: str) -> dict:
    """The durable join facts: fired data artifacts and succeeded events."""
    from support import t042

    rows = [
        row
        for row in observations.node_result_rows(dbname, run_id)
        if row["node_id"] == join_id
    ]
    events = [
        event
        for event in observations.node_events(dbname, run_id)
        if event["payload"]["node_id"] == join_id and event["payload"]["state"] == "succeeded"
    ]
    return {
        "data_rows": len([row for row in rows if row["kind"] == "data"]),
        "artifact_ids": [row["artifact_id"] for row in rows if row["kind"] == "data"],
        "succeeded_event_seqs": [event["seq"] for event in events],
    }


def node_states(dbname: str, run_id: str) -> dict[str, list[str]]:
    from support import t042

    states: dict[str, list[str]] = {}
    for event in observations.node_events(dbname, run_id):
        payload = event["payload"]
        states.setdefault(payload["node_id"], []).append(payload["state"])
    return states


def parallel_calls_definition(child_version_id: str, *, output_key: str = "echo") -> dict:
    """start forks onto two ``call_app`` nodes for the *same* child version.

    The join is a real fork/join pair (``fork_id`` = the start node), so the two
    branches are scheduled concurrently by the real graph scheduler and converge
    once. This is the FAULT-07 parent: one Run, two invocations of one pinned
    child application version.
    """
    nodes = [
        _start_node(),
        _call_app("left", child_version_id, output_key=output_key, literal="alpha"),
        _call_app("right", child_version_id, output_key=output_key, literal="beta"),
        _join("join", fork_id="start", required=("left", "right")),
        _end_node(),
    ]
    edges = [
        {"source": "start", "target": "left"},
        {"source": "start", "target": "right"},
        {"source": "left", "target": "join"},
        {"source": "right", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return {
        "dsl_version": "1.0",
        "input_schema": {"type": "object", "additionalProperties": True},
        "output_schema": dict(RESULTS_OUTPUT_SCHEMA),
        "nodes": nodes,
        "edges": edges,
        "policy": {
            "budget_limit": "1.00",
            "currency": "USD",
            "max_active_seconds": 300,
            "max_child_calls": 12,
            "max_child_depth": 3,
        },
    }


def sealed_manifest(stack, run_id: str) -> dict:
    from support import t027

    snapshot_id = t078.run_row(stack.dbname, run_id)["execution_snapshot_id"]
    return t027.snapshot_row(stack.dbname, snapshot_id)["manifest"]


# ---------------------------------------------------------------------------
# fault records
# ---------------------------------------------------------------------------
def _short(value, limit: int = 900) -> str:
    redacted = redact_value(value)
    text = (
        redacted
        if isinstance(redacted, str)
        else json.dumps(redacted, ensure_ascii=False, default=str)
    )
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass
class Fault:
    """One injected fault and its evidence (expected vs. observed + truth)."""

    fault_id: str
    title: str
    injection_point: str
    expected: list[str] = field(default_factory=list)
    status: str = "not_run"
    input: dict = field(default_factory=dict)
    seed: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    independent_truth: dict = field(default_factory=dict)
    assertions: list[dict] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    not_run: list[str] = field(default_factory=list)
    failure: str | None = None
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    # -- recording ----------------------------------------------------------
    def check(self, name: str, condition, detail=None) -> None:
        ok = bool(condition)
        self.assertions.append(
            {"name": name, "ok": ok, "detail": None if detail is None else _short(detail)}
        )
        assert ok, f"{self.fault_id}: {name} failed: {detail}"

    def observe(self, key: str, value) -> None:
        self.observed[key] = value

    def truth(self, key: str, value) -> None:
        self.independent_truth[key] = value

    def command(self, text: str) -> None:
        self.commands.append(text)

    def block(self, reason: str) -> None:
        self.status = "blocked"
        self.failure = reason

    def mark_passed(self) -> None:
        if not self.assertions:
            raise AssertionError(
                f"{self.fault_id}: cannot be passed without at least one assertion"
            )
        if self.status in {"blocked", "failed"}:
            return
        self.status = "passed"

    # -- evidence -----------------------------------------------------------
    def record(self) -> dict:
        return redact_value(
            {
                "fault_id": self.fault_id,
                "title": self.title,
                "status": self.status,
                "verdict": self.status,
                "injection_point": self.injection_point,
                "input": self.input,
                "seed": self.seed,
                "expected": self.expected,
                "observed": self.observed,
                "independent_truth": self.independent_truth,
                "assertions": self.assertions,
                "assertion_count": len(self.assertions),
                "commands": self.commands,
                "not_run": self.not_run,
                "duration_s": self.duration_s,
                "failure": self.failure,
            }
        )

    def persist(self) -> None:
        self.duration_s = round(time.time() - self.started_at, 2)
        _FAULTS[self.fault_id] = self
        if os.environ.get(WRITE_EVIDENCE_ENV) != "1":
            return
        existing: dict[str, dict] = {}
        if EVIDENCE_JSON.exists():
            try:
                payload = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
                for item in payload.get("faults", []):
                    existing[item["fault_id"]] = item
            except (json.JSONDecodeError, KeyError):  # pragma: no cover - corrupt file
                existing = {}
        existing[self.fault_id] = self.record()
        ordered = [existing[key] for key in sorted(existing)]
        branch, commit = git_branch_and_commit()
        document = {
            "ticket": "T108",
            "issue": 109,
            "title": "Fault injection: same-parent child concurrency, a full child pool "
            "and nested branch joins",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "branch": branch,
            "product_commit": commit,
            "product_commit_note": (
                "HEAD checked out when this evidence run started; the ticket's sources are "
                "committed on the same branch (the evidence file itself is committed after it)"
            ),
            "environment": ENVIRONMENT,
            "faults": ordered,
        }
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        leaks = find_violations(text)
        if leaks:  # pragma: no cover - a credential leak must fail the evidence run
            raise AssertionError(f"credential-shaped values in evidence: {leaks}")
        EVIDENCE_JSON.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_JSON.write_text(text, encoding="utf-8")

    def __enter__(self) -> "Fault":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and self.status not in {"blocked", "failed"}:
            self.status = "failed"
            if self.failure is None:
                self.failure = f"{exc_type.__name__}: {exc}"
            if os.environ.get("F13B_VERBOSE_FAILURES") == "1":
                self.observed["traceback"] = traceback.format_exc(limit=8)
        self.persist()
        return False


_FAULTS: dict[str, Fault] = {}


def start(
    fault_id: str,
    title: str,
    *,
    injection_point: str,
    expected: list[str] | None = None,
) -> Fault:
    """Create and register one fault record (pre-registering its expectations)."""
    fault = Fault(
        fault_id=fault_id,
        title=title,
        injection_point=injection_point,
        expected=list(expected or []),
        not_run=list(ENVIRONMENT["not_run"]),
    )
    _FAULTS[fault_id] = fault
    return fault


def records() -> dict[str, dict]:
    return {key: value.record() for key, value in _FAULTS.items()}


def git_branch_and_commit() -> tuple[str | None, str | None]:
    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(PRODUCT_ROOT), *args],
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            return None
        if completed.returncode != 0:
            return None
        return (completed.stdout or "").strip() or None

    return git("rev-parse", "--abbrev-ref", "HEAD"), git("rev-parse", "HEAD")


__all__ = [name for name in dir() if not name.startswith("_")]
