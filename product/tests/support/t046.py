"""F06/T046 real-stack helpers: shared decimal budget reservation/settlement.

The stack is T041/T045's: a throwaway PostgreSQL database migrated through the
real ``cmd/admin migrate`` chain (so migration 0028 runs), the compiled ``cmd/api``
behind real HTTP, the real internal mTLS listeners, the real filesystem object
store and the real golden manifest. The provider is the documented dev stub; a
profile's registered ``model_id`` selects the deterministic behaviour, including
``stub-usage-missing*`` (a successful answer without a usage object).

Nothing is mocked: reservations are real ``platform.usage_ledger`` rows, the
concurrency gate is a real PostgreSQL row lock held by a real ``psql`` session,
and the test learns the reservation amount from the ledger (or computes the
price-version formula independently with Decimal).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

import pg_dev

from support import stack as st
from support import t029, t041, t045

ONE_UNIT = Decimal("0.00000001")


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def configure_golden(
    dbname: str,
    *,
    model_id: str = "stub-model",
    config: dict | None = None,
) -> str:
    """Point the golden primary profile at a model id and a generation config."""
    t045.set_profile(
        dbname,
        t041.GOLDEN_MODEL_ID,
        model_id=model_id,
        config=config if config is not None else t045.compatible_config(),
    )
    return t041.GOLDEN_MODEL_ID


def missing_usage_config(fallback_ids: list[str] | None = None, **overrides) -> dict:
    """A priced generation profile that the stub answers without usage."""
    return t045.compatible_config(fallback_ids, **overrides)


def set_budget(dbname: str, run_id: str, amount: str) -> None:
    t045.set_run_budget(dbname, run_id, amount)


def expected_reservation(
    params: dict,
    *,
    input_per_million: str,
    output_per_million: str,
    max_output_tokens: int = 4096,
) -> Decimal:
    """The brief's conservative reservation, computed independently in Decimal."""
    canonical = json.dumps(
        params, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    input_tokens = (len(canonical) + 3) // 4
    total = (
        Decimal(input_per_million) * input_tokens / Decimal(1_000_000)
        + Decimal(output_per_million) * max_output_tokens / Decimal(1_000_000)
    )
    return total.quantize(ONE_UNIT, rounding=ROUND_CEILING)


def expected_charge(
    tokens_in: int,
    tokens_out: int,
    *,
    input_per_million: str,
    output_per_million: str,
) -> Decimal:
    total = (
        Decimal(input_per_million) * tokens_in / Decimal(1_000_000)
        + Decimal(output_per_million) * tokens_out / Decimal(1_000_000)
    )
    return total.quantize(ONE_UNIT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# database reads
# ---------------------------------------------------------------------------
def ledger_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text, run_id::text, budget_root_run_id::text, operation_id::text, "
        "entry_key, kind, amount::text, currency, tokens_in, tokens_out, "
        "price_version, created_at::text "
        "FROM platform.usage_ledger "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
        "ORDER BY created_at, entry_key",
    )


def ledger_debits(dbname: str, run_id: str) -> dict:
    """The exact decimal sums the ledger arithmetic defines."""
    return st.sql_rows(
        dbname,
        "SELECT "
        "COALESCE(SUM(CASE WHEN kind = 'reserve' THEN amount ELSE 0 END), 0)::text AS reserved, "
        "COALESCE(SUM(CASE WHEN kind = 'release' THEN amount ELSE 0 END), 0)::text AS released, "
        "COALESCE(SUM(CASE WHEN kind = 'charge' THEN amount ELSE 0 END), 0)::text AS charged, "
        "COALESCE(SUM(CASE WHEN kind = 'reserve' THEN amount "
        "WHEN kind = 'release' THEN -amount ELSE 0 END), 0)::text AS outstanding "
        "FROM platform.usage_ledger "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid",
    )[0]


def attempt_row(dbname: str, operation_id: str, attempt_no: int) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, operation_id::text, attempt_no, state, tokens_in, tokens_out, "
        "reserved_amount::text, charged_amount::text, currency, price_version, "
        "settlement_state, error_code "
        "FROM platform.operation_attempts "
        f"WHERE operation_id = {st.quote_literal(operation_id)}::uuid "
        f"AND attempt_no = {int(attempt_no)}",
    )
    assert len(rows) == 1, rows
    return rows[0]


def budget_facts(owner: t029.Owner, run_id: str) -> dict:
    return t041.ledger_budget(owner.creator, run_id)


def run_error_code(dbname: str, run_id: str) -> str | None:
    value = st.sql_scalar(
        dbname,
        f"SELECT COALESCE(error_code, '') FROM platform.runs WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    return value or None


# ---------------------------------------------------------------------------
# maintenance reconciliation
# ---------------------------------------------------------------------------
def reconcile(
    stack,
    run_id: str,
    *,
    operation_id: str,
    attempt_no: int,
    amount: str,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    price_version: str | None = None,
):
    body = {
        "operation_id": operation_id,
        "attempt_no": attempt_no,
        "amount": amount,
    }
    if tokens_in is not None:
        body["tokens_in"] = tokens_in
    if tokens_out is not None:
        body["tokens_out"] = tokens_out
    if price_version is not None:
        body["price_version"] = price_version
    client = t029.execution_client(stack, "maintenance")
    return client.post(f"/internal/v1/runs/{run_id}/capability-settlements", body)


# ---------------------------------------------------------------------------
# DB-side concurrency gate
# ---------------------------------------------------------------------------
class RunRowGate:
    """Hold one run row's FOR UPDATE lock from a real psql session.

    The capability service takes the same lock in ``prepareOperation`` and the
    budget's root lock in ``ReserveTx``. Both racing HTTP requests therefore
    block on a database lock; the test waits on ``pg_stat_activity`` until both
    are really waiting, then commits. No wall-clock sleep decides the race.
    """

    def __init__(self, dbname: str, run_id: str) -> None:
        self.dbname = dbname
        self.run_id = run_id
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> "RunRowGate":
        env = dict(os.environ)
        env["PGPASSWORD"] = pg_dev.load_secret()
        self._process = subprocess.Popen(
            [
                pg_dev.psql_binary(), "-X", "-q", "-A", "-t",
                "-h", "127.0.0.1", "-p", str(pg_dev.DEFAULT_PORT),
                "-U", pg_dev.DEFAULT_USER, "-d", self.dbname,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        assert self._process.stdin is not None
        self._process.stdin.write(
            "BEGIN;\n"
            f"SELECT id FROM platform.runs WHERE id = {st.quote_literal(self.run_id)}::uuid FOR UPDATE;\n"
        )
        self._process.stdin.flush()
        self._wait_for(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND state = 'idle in transaction' "
            "AND query LIKE 'SELECT id FROM platform.runs%'",
            minimum=1,
            label="the run row lock",
        )
        return self

    def wait_for_waiters(self, count: int) -> None:
        self._wait_for(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'",
            minimum=count,
            label=f"{count} HTTP requests waiting on the database lock",
        )

    def _wait_for(self, query: str, *, minimum: int, label: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if int(st.sql_scalar(self.dbname, query)) >= minimum:
                return
            time.sleep(0.02)
        raise AssertionError(f"timed out waiting for {label}")

    def release(self) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write("COMMIT;\n")
        self._process.stdin.flush()
        self._process.stdin.close()
        self._process.wait(timeout=10)

    def __exit__(self, *exc_info) -> None:
        if self._process is not None and self._process.poll() is None:
            self.release()


def race_invocations(
    stack, lease: dict, *, operation_ids: list[str], params: dict, logical_keys: list[str] | None = None
) -> list:
    """Start N real invocations together behind the database run-row gate."""
    results: list[object] = [None] * len(operation_ids)
    barrier = threading.Barrier(len(operation_ids) + 1, timeout=30)
    if logical_keys is None:
        logical_keys = [f"race-{index + 1}" for index in range(len(operation_ids))]
    assert len(logical_keys) == len(operation_ids)

    def worker(index: int) -> None:
        client = t041.worker_client(stack)
        barrier.wait()
        try:
            results[index] = t041.invoke(
                client, lease, operation_id=operation_ids[index],
                logical_key=logical_keys[index], params=params,
            )
        except Exception as error:  # pragma: no cover - surfaced by the assertion
            results[index] = error

    with RunRowGate(stack.dbname, lease["run_id"]) as gate:
        threads = [threading.Thread(target=worker, args=(index,)) for index in range(len(operation_ids))]
        for thread in threads:
            thread.start()
        barrier.wait()
        gate.wait_for_waiters(len(operation_ids))
        gate.release()
        for thread in threads:
            thread.join(timeout=60)
            assert not thread.is_alive(), "a raced invocation did not finish"
    for result in results:
        assert result is not None and not isinstance(result, Exception), result
    return results
