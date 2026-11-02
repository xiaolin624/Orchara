"""Deterministic issuance-versus-disable races on the real database.

A ``BEFORE INSERT`` trigger calls ``pg_sleep`` so the issuance transaction stays
open while the concurrent disable / handover runs from another real client. The
test does not guess a wall-clock delay: it polls ``pg_stat_activity`` (scoped to
this test's throwaway database) until the trigger's sleep is visible, which
proves the INSERT -- and, with the fix, the ``ROLESET1`` advisory lock that the
issuance took before it -- is already in flight.

This is the same real-stack seam as the rest of ``tests/f01``: a throwaway
PostgreSQL database, the compiled binaries and real HTTP. Nothing is mocked. A
trigger is test infrastructure on the real table, not a substitute for it, and
every trigger/function is dropped in ``finally``; the database itself is dropped
by the ``fresh_database`` fixture even when teardown fails.
"""

from __future__ import annotations

import threading
import time

from conftest import sql


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def install_sleep_trigger(
    database: str,
    *,
    table: str,
    trigger: str,
    function: str,
    seconds: float = 0.8,
    only_kind: str | None = None,
) -> None:
    """Install a ``BEFORE INSERT`` trigger that sleeps (optionally for one kind).

    ``table`` / ``trigger`` / ``function`` are test constants, never user input.
    The function is created unqualified (the owner's default schema) and the
    trigger names it by OID, so the API's own ``search_path`` does not matter.
    """
    body = ["BEGIN"]
    if only_kind is not None:
        body.append(f"    IF NEW.kind = {_literal(only_kind)} THEN")
        body.append(f"        PERFORM pg_sleep({float(seconds)});")
        body.append("    END IF;")
    else:
        body.append(f"    PERFORM pg_sleep({float(seconds)});")
    body.extend(["    RETURN NEW;", "END;"])
    sql(
        database,
        f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger "
        f"LANGUAGE plpgsql AS $f01_race$\n" + "\n".join(body) + "\n$f01_race$;\n"
        f"DROP TRIGGER IF EXISTS {trigger} ON {table};\n"
        f"CREATE TRIGGER {trigger} BEFORE INSERT ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()",
    )


def drop_sleep_trigger(database: str, *, table: str, trigger: str, function: str) -> None:
    """Remove the trigger and its function; safe to call even if creation failed."""
    sql(
        database,
        f"DROP TRIGGER IF EXISTS {trigger} ON {table};\n"
        f"DROP FUNCTION IF EXISTS {function}()",
    )


def wait_for_sleeping_insert(database: str, timeout: float = 10.0) -> None:
    """Block until the trigger's ``pg_sleep`` is visible for this database.

    ``wait_event = 'PgSleep'`` appears exactly while the trigger holds the
    issuance transaction open, i.e. after the transaction began (and, with the
    lock fix, after it acquired ``ROLESET1``) and before it commits. That makes
    the concurrent step deterministic: the disable/handover can only race the
    window the test is trying to prove, not an earlier or later phase.
    """
    deadline = time.time() + timeout
    observed = "0"
    while time.time() < deadline:
        observed = sql(
            database,
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "AND wait_event_type = 'Timeout' AND wait_event = 'PgSleep'",
        )
        if int(observed) > 0:
            return
        time.sleep(0.02)
    raise AssertionError(
        "the issuance trigger never entered pg_sleep within "
        f"{timeout}s (last observed count: {observed})"
    )


class Issuance:
    """Run one real HTTP issuance on a worker thread and collect its outcome.

    Assertions stay on the main thread: a failing ``issuance.value`` assertion
    is the test's own failure, while an exception raised inside the request is
    re-raised from ``join`` so the real error is never swallowed.
    """

    def __init__(self, target) -> None:  # noqa: ANN001 - a zero-arg callable
        self._target = target
        self.value = None
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.value = self._target()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            self.error = exc

    def start(self) -> "Issuance":
        self._thread.start()
        return self

    def join(self, timeout: float = 30.0) -> None:
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise AssertionError("the issuance request did not finish in time")
        if self.error is not None:
            raise self.error
