#!/usr/bin/env python3
"""P02/T005 probe: an interrupted run continues safely in a new process.

What it demonstrates, against a real PostgreSQL cluster:

* checkpointer tables are created and read/written for real;
* the **runtime** (run/resume and the approval write) connects as the restricted
  `orchara_worker` role, which cannot run DDL and cannot write another module's
  table;
* the runtime refuses to start when its ``search_path`` does not resolve the
  checkpointer tables, instead of silently creating them somewhere else;
* a run that stops at an approval boundary resumes in a *separate process* and
  still sees the original state and decision;
* a different owner cannot read the thread;
* replaying the same logical operation does not create a second row.

Commands (each one is a separate OS process, which is the point):

    python checkpoint_probe.py init
    python checkpoint_probe.py setup-checkpointer
    python checkpoint_probe.py run    --thread T1 --owner alice
    python checkpoint_probe.py resume --thread T1 --owner alice --decision approve
    python checkpoint_probe.py read   --thread T1 --owner alice
    python checkpoint_probe.py replay --thread T1 --owner alice --times 3
    python checkpoint_probe.py guards
"""

from __future__ import annotations

import argparse
import json
import sys
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

# Connection details come from the shared development-cluster tool. No
# credential lives in this file (product/AGENTS.md hard rule).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import pg_dev  # noqa: E402

CHECKPOINT_SCHEMA = "checkpoint"
APP_SCHEMA = "app"
WORKER_ROLE = "orchara_worker"
EXPECTED_CHECKPOINT_TABLE = "checkpoints"


# ---------------------------------------------------------------------------
# connections: owner = control plane, worker = runtime
# ---------------------------------------------------------------------------
def owner_dsn(schema: str | None = None) -> str:
    return pg_dev.dsn_with_schema(schema)


def worker_dsn(schema: str | None = CHECKPOINT_SCHEMA) -> str:
    return pg_dev.worker_dsn(schema)


def _connect(dsn: str):
    return psycopg.connect(dsn, autocommit=True)


def _current_user(dsn: str) -> str:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user")
        return cur.fetchone()[0]


def assert_runtime_search_path(schema: str | None = CHECKPOINT_SCHEMA) -> dict[str, Any]:
    """Fail closed when the runtime's search_path cannot see the checkpointer.

    Without this the runtime would happily let the checkpointer create its
    tables in whatever schema happens to be first on the path.
    """
    with _connect(worker_dsn(schema)) as conn, conn.cursor() as cur:
        cur.execute("SHOW search_path")
        search_path = cur.fetchone()[0]
        cur.execute("SELECT to_regclass(%s)::text", (EXPECTED_CHECKPOINT_TABLE,))
        resolved = cur.fetchone()[0]
        cur.execute("SELECT current_user")
        role = cur.fetchone()[0]
    if resolved is None:
        _fail(
            "wrong_search_path",
            search_path=search_path,
            expected_table=EXPECTED_CHECKPOINT_TABLE,
            schema_checked=schema,
            runtime_role=role,
        )
    return {
        "search_path": search_path,
        "resolved_table": resolved,
        "runtime_role": role,
    }


# ---------------------------------------------------------------------------
# schema, roles and tables
# ---------------------------------------------------------------------------
def init() -> dict[str, Any]:
    owner = owner_dsn()
    worker_password = pg_dev.load_worker_secret()
    with _connect(owner) as conn, conn.cursor() as cur:
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
                thread_id  text PRIMARY KEY,
                owner      text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
                    CREATE ROLE {WORKER_ROLE} LOGIN;
                END IF;
            END
            $$
            """
        )
        # Keep the role's password in sync with the operator's local secret.
        # ALTER ROLE does not accept a bind parameter, so quote it explicitly.
        from psycopg import sql as psql  # local import keeps the module imports tidy

        cur.execute(
            psql.SQL("ALTER ROLE {} PASSWORD {}").format(
                psql.Identifier(WORKER_ROLE), psql.Literal(worker_password)
            )
        )
        cur.execute(f"GRANT USAGE ON SCHEMA {CHECKPOINT_SCHEMA} TO {WORKER_ROLE}")
        cur.execute(
            f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {CHECKPOINT_SCHEMA} TO {WORKER_ROLE}"
        )
        cur.execute(f"GRANT USAGE ON SCHEMA {APP_SCHEMA} TO {WORKER_ROLE}")
        # The runtime owns its own outbox (`operations`) ...
        cur.execute(f"GRANT SELECT, INSERT ON {APP_SCHEMA}.operations TO {WORKER_ROLE}")
        # ... but may only read the identity module's table, never write it.
        cur.execute(f"GRANT SELECT ON {APP_SCHEMA}.threads TO {WORKER_ROLE}")
        cur.execute(f"REVOKE INSERT, UPDATE, DELETE ON {APP_SCHEMA}.threads FROM {WORKER_ROLE}")
        cur.execute(f"REVOKE CREATE ON SCHEMA {APP_SCHEMA} FROM {WORKER_ROLE}")
        cur.execute(f"REVOKE CREATE ON SCHEMA {CHECKPOINT_SCHEMA} FROM {WORKER_ROLE}")
        cur.execute(f"ALTER ROLE {WORKER_ROLE} SET search_path = {CHECKPOINT_SCHEMA}, public")
    return {"initialised": True, "schemas": [CHECKPOINT_SCHEMA, APP_SCHEMA], "worker_role": WORKER_ROLE}


def setup_checkpointer() -> dict[str, Any]:
    """Create the checkpoint tables inside the dedicated schema (owner only)."""
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
    # Declared as channels so they survive the checkpoint round trip.
    thread_id: str
    owner: str


def _build_graph(saver: PostgresSaver):
    def prepare(state: RunState) -> RunState:
        return {"stage": "prepared", "amount": state.get("amount", "0")}

    def approve(state: RunState) -> RunState:
        # The runtime performs the write, as the restricted role.
        with _connect(worker_dsn()) as conn, conn.cursor() as cur:
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
    return graph.compile(checkpointer=saver, interrupt_before=["approve"])


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _register_thread(thread_id: str, owner: str) -> None:
    # Registering a thread is a control-plane action, so it uses the owner role.
    with _connect(owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {APP_SCHEMA}.threads (thread_id, owner) VALUES (%s, %s)
            ON CONFLICT (thread_id) DO NOTHING
            """,
            (thread_id, owner),
        )


def _assert_owner(thread_id: str, owner: str) -> None:
    with _connect(owner_dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT owner FROM {APP_SCHEMA}.threads WHERE thread_id = %s", (thread_id,))
        row = cur.fetchone()
    if row is None:
        _fail("thread_not_found", thread=thread_id)
    if row[0] != owner:
        _fail("thread_not_owned", thread=thread_id, owner=owner)


def _fail(code: str, **extra: Any) -> None:
    """Emit a machine-readable failure on stdout and exit non-zero."""
    print(json.dumps({"error": code, **extra}, ensure_ascii=False, default=str))
    raise SystemExit(2)


def run(thread_id: str, owner: str) -> dict[str, Any]:
    _register_thread(thread_id, owner)
    runtime = assert_runtime_search_path()
    with PostgresSaver.from_conn_string(worker_dsn()) as saver:
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
        "runtime_role": runtime["runtime_role"],
        "search_path": runtime["search_path"],
    }


def resume(thread_id: str, owner: str, decision: str) -> dict[str, Any]:
    _assert_owner(thread_id, owner)
    runtime = assert_runtime_search_path()
    with PostgresSaver.from_conn_string(worker_dsn()) as saver:
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
        "runtime_role": runtime["runtime_role"],
    }


def read(thread_id: str, owner: str) -> dict[str, Any]:
    _assert_owner(thread_id, owner)
    with PostgresSaver.from_conn_string(worker_dsn()) as saver:
        graph = _build_graph(saver)
        snapshot = graph.get_state(_config(thread_id))
    return {
        "thread": thread_id,
        "owner": owner,
        "values": snapshot.values,
        "pending_nodes": list(snapshot.next),
        "runtime_role": _current_user(worker_dsn()),
    }


def replay(thread_id: str, owner: str, times: int = 3) -> dict[str, Any]:
    """Repeat the same logical write and report how many rows exist."""
    _assert_owner(thread_id, owner)
    key = f"op-{thread_id}"
    with _connect(worker_dsn()) as conn, conn.cursor() as cur:
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
    return {
        "thread": thread_id,
        "attempts": times,
        "operation_rows": rows,
        "idempotency_key": key,
        "runtime_role": _current_user(worker_dsn()),
    }


# ---------------------------------------------------------------------------
# guards: DDL, cross-module writes, search_path
# ---------------------------------------------------------------------------
def guards() -> dict[str, Any]:
    results: dict[str, Any] = {}

    # 1. DDL must be impossible for the runtime role.
    try:
        with _connect(worker_dsn()) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE checkpoint.should_not_exist (id int)")
        results["ddl"] = {"blocked": False, "detail": "runtime role was able to run DDL"}
    except psycopg.errors.InsufficientPrivilege as exc:
        results["ddl"] = {"blocked": True, "detail": str(exc).splitlines()[0]}

    # 2. Writing another module's table must be impossible.
    try:
        with _connect(worker_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {APP_SCHEMA}.threads (thread_id, owner)"
                " VALUES ('forged', 'mallory')"
            )
        results["cross_module_write"] = {
            "blocked": False,
            "detail": "runtime role wrote app.threads",
        }
    except psycopg.errors.InsufficientPrivilege as exc:
        results["cross_module_write"] = {"blocked": True, "detail": str(exc).splitlines()[0]}

    # 3. The runtime's own search_path guard must reject a wrong path.
    healthy = assert_runtime_search_path()
    results["search_path"] = {
        "runtime_role": healthy["runtime_role"],
        "correct_search_path_resolves": healthy["resolved_table"] is not None,
    }
    try:
        with _connect(worker_dsn("public")) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)::text", (EXPECTED_CHECKPOINT_TABLE,))
            resolved = cur.fetchone()[0]
        results["search_path"]["wrong_search_path_resolves"] = resolved is not None
        results["search_path"]["detected"] = resolved is None
    except psycopg.Error as exc:  # pragma: no cover - defensive
        results["search_path"]["detected"] = False
        results["search_path"]["error"] = str(exc)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P02 checkpoint/resume probe")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("setup-checkpointer")
    sub.add_parser("guards")
    runtime_check = sub.add_parser("runtime-check")
    runtime_check.add_argument("--schema", default=CHECKPOINT_SCHEMA)

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
    elif args.command == "runtime-check":
        payload = assert_runtime_search_path(args.schema)
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
