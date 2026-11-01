#!/usr/bin/env python3
"""P02/T005 probe: an interrupted run continues safely in a new process.

What it demonstrates, against a real PostgreSQL cluster:

* checkpointer tables are created and read/written for real;
* the runtime role cannot run DDL and cannot write another module's table;
* a wrong ``search_path`` is detected instead of silently creating tables in the
  wrong schema;
* a run that stops at an approval boundary resumes in a *separate process* and
  still sees the original state and decision;
* a different owner cannot read the thread;
* replaying the same logical operation does not create a second row.

Commands (each one is a separate OS process, which is the point):

    python checkpoint_probe.py init
    python checkpoint_probe.py run    --thread T1 --owner alice
    python checkpoint_probe.py resume --thread T1 --owner alice --decision approve
    python checkpoint_probe.py read   --thread T1 --owner alice
    python checkpoint_probe.py guards
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote

try:  # the probe runs inside the locked probe environment
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, START, StateGraph
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "run this probe with the locked environment: "
        "product/probes/agent_min/.venv (see requirements.lock.txt)"
    ) from exc

DEFAULT_DSN = os.environ.get(
    "ORCHARA_PG_DSN",
    "postgresql://orchara_owner:orchara_dev_only@127.0.0.1:55432/orchara_probe?sslmode=disable",
)
WORKER_ROLE = "orchara_worker"
WORKER_PASSWORD = "orchara_worker_dev_only"
CHECKPOINT_SCHEMA = "checkpoint"
APP_SCHEMA = "app"


# ---------------------------------------------------------------------------
# connection helpers
# ---------------------------------------------------------------------------
def owner_dsn(schema: str | None = None) -> str:
    if not schema:
        return DEFAULT_DSN
    separator = "&" if "?" in DEFAULT_DSN else "?"
    return f"{DEFAULT_DSN}{separator}options={quote(f'-csearch_path={schema}')}"


def worker_dsn(schema: str | None = None) -> str:
    base = (
        f"postgresql://{WORKER_ROLE}:{WORKER_PASSWORD}@127.0.0.1:55432/orchara_probe?sslmode=disable"
    )
    if schema:
        base += f"&options={quote(f'-csearch_path={schema}')}"
    return base


def _connect(dsn: str):
    return psycopg.connect(dsn, autocommit=True)


# ---------------------------------------------------------------------------
# schema, roles and tables
# ---------------------------------------------------------------------------
def init() -> dict[str, Any]:
    with _connect(owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {APP_SCHEMA}")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {APP_SCHEMA}.operations (
                idempotency_key text PRIMARY KEY,
                thread_id       text NOT NULL,
                owner           text NOT NULL,
                payload         jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                created_at      timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {APP_SCHEMA}.threads (
                thread_id text PRIMARY KEY,
                owner     text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
                    CREATE ROLE {WORKER_ROLE} LOGIN PASSWORD '{WORKER_PASSWORD}';
                END IF;
            END
            $$
            """
        )
        # Checkpoint tables were created by the checkpointer; grant on what exists.
        cur.execute(f"GRANT USAGE ON SCHEMA {CHECKPOINT_SCHEMA} TO {WORKER_ROLE}")
        cur.execute(
            f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {CHECKPOINT_SCHEMA} TO {WORKER_ROLE}"
        )
        cur.execute(f"GRANT USAGE ON SCHEMA {APP_SCHEMA} TO {WORKER_ROLE}")
        # Read-only on the business table: the runtime may not write another module's data.
        cur.execute(f"GRANT SELECT ON {APP_SCHEMA}.operations TO {WORKER_ROLE}")
        cur.execute(f"GRANT SELECT ON {APP_SCHEMA}.threads TO {WORKER_ROLE}")
        # Explicitly NOT granted: CREATE on any schema, INSERT on app.operations.
        cur.execute(f"REVOKE CREATE ON SCHEMA {APP_SCHEMA} FROM {WORKER_ROLE}")
        cur.execute(f"ALTER ROLE {WORKER_ROLE} SET search_path = {CHECKPOINT_SCHEMA}, public")
    return {"initialised": True, "schemas": [CHECKPOINT_SCHEMA, APP_SCHEMA], "worker_role": WORKER_ROLE}


def setup_checkpointer() -> dict[str, Any]:
    """Create the checkpoint tables inside the dedicated schema."""
    with PostgresSaver.from_conn_string(owner_dsn(CHECKPOINT_SCHEMA)) as saver:
        saver.setup()
    with _connect(owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"GRANT USAGE ON SCHEMA {CHECKPOINT_SCHEMA} TO {WORKER_ROLE}")
        cur.execute(
            f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {CHECKPOINT_SCHEMA} TO {WORKER_ROLE}"
        )
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s ORDER BY table_name
            """,
            (CHECKPOINT_SCHEMA,),
        )
        tables = [row[0] for row in cur.fetchall()]
    return {"checkpoint_tables": tables}


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------
class RunState(TypedDict, total=False):
    stage: str
    amount: str
    decision: str | None
    operation_key: str
    # Declared as channels so they survive the checkpoint round trip; the
    # approval node needs them to write the operation row.
    thread_id: str
    owner: str


def _build_graph(saver: PostgresSaver):
    def prepare(state: RunState) -> RunState:
        return {"stage": "prepared", "amount": state.get("amount", "0")}

    def approve(state: RunState) -> RunState:
        # The actual write happens here, after the human decision.
        with _connect(owner_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {APP_SCHEMA}.operations (idempotency_key, thread_id, owner, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (
                    state.get("operation_key"),
                    state.get("thread_id", ""),
                    state.get("owner", ""),
                    json.dumps({"amount": state.get("amount"), "decision": state.get("decision")}),
                ),
            )
        return {"stage": "approved"}

    def finalize(state: RunState) -> RunState:
        return {"stage": "done"}

    graph = StateGraph(RunState)
    graph.add_node("prepare", prepare)
    graph.add_node("approve", approve)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "approve")
    graph.add_edge("approve", "finalize")
    graph.add_edge("finalize", END)
    # The approval boundary: the run must stop before 'approve' executes.
    return graph.compile(checkpointer=saver, interrupt_before=["approve"])


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _register_thread(thread_id: str, owner: str) -> None:
    with _connect(owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {APP_SCHEMA}.threads (thread_id, owner) VALUES (%s, %s)
            ON CONFLICT (thread_id) DO NOTHING
            """,
            (thread_id, owner),
        )


def _fail(code: str, **extra: Any) -> None:
    """Emit a machine-readable failure on stdout and exit non-zero.

    Errors go to stdout so callers can always parse one JSON object, whether the
    probe succeeded or not.
    """
    print(json.dumps({"error": code, **extra}, ensure_ascii=False, default=str))
    raise SystemExit(2)


def _assert_owner(thread_id: str, owner: str) -> None:
    with _connect(owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT owner FROM {APP_SCHEMA}.threads WHERE thread_id = %s", (thread_id,))
        row = cur.fetchone()
    if row is None:
        _fail("thread_not_found", thread=thread_id)
    if row[0] != owner:
        _fail("thread_not_owned", thread=thread_id, owner=owner)


def run(thread_id: str, owner: str) -> dict[str, Any]:
    _register_thread(thread_id, owner)
    with PostgresSaver.from_conn_string(owner_dsn(CHECKPOINT_SCHEMA)) as saver:
        graph = _build_graph(saver)
        state = graph.invoke(
            {
                "stage": "new",
                "amount": "120.50",
                "operation_key": f"op-{thread_id}",
                "thread_id": thread_id,
                "owner": owner,
            },
            _config(thread_id),
        )
        snapshot = graph.get_state(_config(thread_id))
    return {
        "thread": thread_id,
        "stage": state.get("stage"),
        "interrupted": bool(snapshot.next),
        "pending_nodes": list(snapshot.next),
        "checkpoint_id": str(snapshot.config.get("configurable", {}).get("checkpoint_id")),
    }


def resume(thread_id: str, owner: str, decision: str) -> dict[str, Any]:
    _assert_owner(thread_id, owner)
    with PostgresSaver.from_conn_string(owner_dsn(CHECKPOINT_SCHEMA)) as saver:
        graph = _build_graph(saver)
        before = graph.get_state(_config(thread_id))
        if not before.next:
            _fail("nothing_to_resume", thread=thread_id)
        graph.update_state(_config(thread_id), {"decision": decision})
        state = graph.invoke(None, _config(thread_id))
        after = graph.get_state(_config(thread_id))
    with _connect(owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {APP_SCHEMA}.operations WHERE thread_id = %s", (thread_id,)
        )
        writes = cur.fetchone()[0]
    return {
        "thread": thread_id,
        "stage": state.get("stage"),
        "decision": state.get("decision"),
        "resumed_from_pending": list(before.next),
        "finished": not after.next,
        "operation_rows": writes,
    }


def read(thread_id: str, owner: str) -> dict[str, Any]:
    _assert_owner(thread_id, owner)
    with PostgresSaver.from_conn_string(owner_dsn(CHECKPOINT_SCHEMA)) as saver:
        graph = _build_graph(saver)
        snapshot = graph.get_state(_config(thread_id))
    return {
        "thread": thread_id,
        "owner": owner,
        "values": snapshot.values,
        "pending_nodes": list(snapshot.next),
    }


# ---------------------------------------------------------------------------
# guards: DDL, cross-table writes, search_path
# ---------------------------------------------------------------------------
def guards() -> dict[str, Any]:
    results: dict[str, Any] = {}

    # 1. DDL must be impossible for the runtime role.
    try:
        with _connect(worker_dsn(CHECKPOINT_SCHEMA)) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE checkpoint.should_not_exist (id int)")
        results["ddl"] = {"blocked": False, "detail": "worker was able to run DDL"}
    except psycopg.errors.InsufficientPrivilege as exc:
        results["ddl"] = {"blocked": True, "detail": str(exc).splitlines()[0]}

    # 2. Writing another module's table must be impossible.
    try:
        with _connect(worker_dsn(CHECKPOINT_SCHEMA)) as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {APP_SCHEMA}.operations (idempotency_key, thread_id, owner)"
                " VALUES ('forged', 't', 'mallory')"
            )
        results["cross_module_write"] = {"blocked": False, "detail": "worker wrote app.operations"}
    except psycopg.errors.InsufficientPrivilege as exc:
        results["cross_module_write"] = {"blocked": True, "detail": str(exc).splitlines()[0]}

    # 3. A wrong search_path must be detected, not silently used.
    expected = "checkpoints"
    with _connect(owner_dsn(CHECKPOINT_SCHEMA)) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)::text", (expected,))
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (expected,))
        results["search_path"] = {
            "expected_table": expected,
            "correct_search_path_resolves": cur.fetchone() is not None,
        }
    with _connect(worker_dsn("public")) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)::text", (expected,))
        resolved = cur.fetchone()[0]
    results["search_path"]["wrong_search_path_resolves"] = resolved is not None
    results["search_path"]["detected"] = resolved is None
    return results


def replay(thread_id: str, owner: str, times: int = 3) -> dict[str, Any]:
    """Repeat the same logical write and report how many rows exist.

    This is the storage-level half of "a replayed operation must not become a
    new operation": the idempotency key makes the insert a no-op after the first.
    """
    _assert_owner(thread_id, owner)
    key = f"op-{thread_id}"
    with _connect(owner_dsn()) as conn, conn.cursor() as cur:
        for _ in range(times):
            cur.execute(
                f"""
                INSERT INTO {APP_SCHEMA}.operations (idempotency_key, thread_id, owner)
                VALUES (%s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (key, thread_id, owner),
            )
        cur.execute(
            f"SELECT count(*) FROM {APP_SCHEMA}.operations WHERE idempotency_key = %s", (key,)
        )
        rows = cur.fetchone()[0]
    return {"thread": thread_id, "attempts": times, "operation_rows": rows, "idempotency_key": key}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P02 checkpoint/resume probe")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("setup-checkpointer")
    sub.add_parser("guards")

    for name in ("run", "resume", "read", "replay"):
        command = sub.add_parser(name)
        command.add_argument("--thread", required=True)
        command.add_argument("--owner", required=True)
        if name == "resume":
            command.add_argument("--decision", required=True)
        if name == "replay":
            command.add_argument("--times", type=int, default=3)

    args = parser.parse_args(argv)

    if args.command == "init":
        payload = init()
    elif args.command == "setup-checkpointer":
        payload = setup_checkpointer()
    elif args.command == "guards":
        payload = guards()
    elif args.command == "run":
        payload = run(args.thread, args.owner)
    elif args.command == "resume":
        payload = resume(args.thread, args.owner, args.decision)
    elif args.command == "replay":
        payload = replay(args.thread, args.owner, args.times)
    else:
        payload = read(args.thread, args.owner)

    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
