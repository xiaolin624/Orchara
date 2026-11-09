"""F07/T081 real-stack helpers: the child pool, per-parent cap and terminal hook.

The stack is T029's (throwaway PostgreSQL migrated by the real ``cmd/admin``
chain, the compiled ``cmd/api`` over real HTTP/mTLS with the signed-link object
store) plus the T033 supervisor binary. Child Runs are always created by the real
``spawnChild`` endpoint; parents are claimed and parked through the real internal
protocol (claim -> spawnChild -> waiting_children proposal -> exit evidence).

Only two things are read directly from PostgreSQL: the authoritative quota
counters/allocations and the Run/waiting rows. Nothing here writes a business
transition with SQL.
"""

from __future__ import annotations

from support import run_observations as observations
from support import t027, t029, t035, t078
from support import stack as st

# T081's independent child pool and the per-parent scope prefix.
CHILD_SCOPE = "global"
CHILD_DIMENSION = "child_slots"
PARENT_SCOPE_PREFIX = "parent:"


# ---------------------------------------------------------------------------
# quota read/write models (capacity is deployment configuration, not a state
# transition; the tests set it through SQL exactly like the existing T029 tests)
# ---------------------------------------------------------------------------
def _counter_row(dbname: str, scope: str, dimension: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT scope, dimension, used, capacity FROM platform.quota_counters "
        f"WHERE scope = {st.quote_literal(scope)} "
        f"AND dimension = {st.quote_literal(dimension)}",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def child_counter(dbname: str) -> dict | None:
    return _counter_row(dbname, CHILD_SCOPE, CHILD_DIMENSION)


def execution_counter(dbname: str) -> dict:
    row = _counter_row(dbname, t029.QUOTA_SCOPE, t029.QUOTA_DIMENSION)
    assert row is not None, "the 0016 execution counter is missing"
    return row


def parent_counter(dbname: str, parent_run_id: str) -> dict | None:
    return _counter_row(dbname, PARENT_SCOPE_PREFIX + parent_run_id, CHILD_DIMENSION)


def _set_capacity(dbname: str, scope: str, dimension: str, capacity: int) -> None:
    assert capacity >= 0
    st.sql(
        dbname,
        "UPDATE platform.quota_counters SET capacity = "
        f"{int(capacity)} WHERE scope = {st.quote_literal(scope)} "
        f"AND dimension = {st.quote_literal(dimension)}",
    )


def set_execution_capacity(dbname: str, capacity: int) -> None:
    _set_capacity(dbname, t029.QUOTA_SCOPE, t029.QUOTA_DIMENSION, capacity)


def set_child_capacity(dbname: str, capacity: int) -> None:
    _set_capacity(dbname, CHILD_SCOPE, CHILD_DIMENSION, capacity)


def set_parent_capacity(dbname: str, parent_run_id: str, capacity: int) -> None:
    _set_capacity(dbname, PARENT_SCOPE_PREFIX + parent_run_id, CHILD_DIMENSION, capacity)


def allocation_rows(dbname: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, scope, dimension, holder_key, "
        "released_at::text AS released_at FROM platform.quota_allocations "
        "ORDER BY scope, dimension, holder_key",
    )


def allocations_for_job(dbname: str, job_id: str) -> list[dict]:
    return [
        row
        for row in allocation_rows(dbname)
        if row["holder_key"] == "job:" + job_id
    ]


def release_ledger(dbname: str) -> dict[tuple[str, str, str], str | None]:
    """Every allocation row keyed by (scope, dimension, holder) -> released_at."""
    return {
        (row["scope"], row["dimension"], row["holder_key"]): row["released_at"]
        for row in allocation_rows(dbname)
    }


def live_allocation_count(dbname: str, dimension: str = CHILD_DIMENSION) -> int:
    return sum(
        1
        for row in allocation_rows(dbname)
        if row["dimension"] == dimension and row["released_at"] is None
    )


# ---------------------------------------------------------------------------
# parent construction and the real waiting hand-over
# ---------------------------------------------------------------------------
def parent_application(stack, creator, definition: dict) -> str:
    """Create the parent application once; each parent Run is a real debug Run."""
    return t027.application_with_definition(creator, definition)


def submit_parent_run(stack, creator, application_id: str, input_value: dict) -> dict:
    response = t027.submit_debug(
        creator,
        application_id,
        draft_revision=t027.draft_revision(creator, application_id),
        input_value=input_value,
    )
    assert response.status == 202, response.text
    return response.json


def claim_lease(stack, *, worker_id: str = "t081-supervisor",
                execution_unit_id: str = "unit-t081") -> dict | None:
    """One real internal claim; ``None`` means 204 (nothing claimable)."""
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id=worker_id, execution_unit_id=execution_unit_id
    )
    assert response.status in (200, 204), response.text
    return response.json if response.status == 200 else None


def claim_child(stack, expected_child_id: str | None = None, **kwargs) -> dict:
    lease = claim_lease(stack, **kwargs)
    assert lease is not None, "no child Run was claimable"
    if expected_child_id is not None:
        assert lease["run_id"] == expected_child_id, (lease, expected_child_id)
    return lease


def spawn_child(stack, parent_run_id: str, lease: dict, *, logical_key: str,
                application_version_id: str, input_value: dict) -> dict:
    response = t078.spawn(
        stack,
        parent_run_id,
        attempt_token=lease["attempt_token"],
        logical_key=logical_key,
        application_version_id=application_version_id,
        input=input_value,
    )
    assert response.status == 202, response.text
    return response.json


def park_waiting(stack, lease: dict, *, child_run_id: str) -> None:
    """Store the waiting_children proposal at ``child:<id>`` and confirm the exit."""
    worker = t035.execution_client(stack, "worker")
    proposed = t035.submit_waiting(
        worker, lease, kind=t035.KIND_CHILDREN, checkpoint_id="child:" + child_run_id
    )
    assert proposed.status == 200, proposed.text
    exited = t035.confirm_waiting_exit(
        stack, lease, execution_unit_id=lease_execution_unit(lease)
    )
    assert exited.status == 200, exited.text


def lease_execution_unit(lease: dict) -> str:
    return lease.get("_execution_unit_id", "unit-t081")


def waiting_parent(stack, creator, application_id: str, child_version_id: str,
                   *, source: str = "source", logical_key: str = "call_app:child") -> dict:
    """One claimed parent Run that spawned a child and parked in waiting_children."""
    parent = submit_parent_run(stack, creator, application_id, {"source": source})
    lease = claim_lease(stack)
    assert lease is not None and lease["run_id"] == parent["id"], (lease, parent)
    lease["_execution_unit_id"] = "unit-t081"
    child = spawn_child(
        stack, parent["id"], lease, logical_key=logical_key,
        application_version_id=child_version_id, input_value={"source": source},
    )
    park_waiting(stack, lease, child_run_id=child["id"])
    return {"parent": parent, "lease": lease, "child": child}


# ---------------------------------------------------------------------------
# real child execution / terminal driving
# ---------------------------------------------------------------------------
def execute_once(stack, **env_kwargs):
    return t078.execute_once(stack, **env_kwargs)


def run_row(dbname: str, run_id: str) -> dict:
    """The T078 Run read model plus the T081 fair-claiming rotation column."""
    row = t078.run_row(dbname, run_id)
    extra = st.sql_rows(
        dbname,
        "SELECT last_child_claim_at::text AS last_child_claim_at FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(extra) == 1, extra
    row.update(extra[0])
    return row


def waiting_row(dbname: str, run_id: str) -> dict | None:
    return t035.waiting_row(dbname, run_id)


def waiting_decision(dbname: str, run_id: str) -> dict | None:
    return t035.waiting_decision(dbname, run_id)


def resume_jobs(dbname: str, run_id: str) -> list[dict]:
    return t035.resume_jobs(dbname, run_id)


def job_rows_for_run(dbname: str, run_id: str) -> list[dict]:
    return t078.job_for_run(dbname, run_id)


def run_children_rows(dbname: str, parent_run_id: str) -> list[dict]:
    return t078.run_children_rows(dbname, parent_run_id)


def wait_for(predicate, *, timeout: float = 10.0, interval: float = 0.05):
    """Poll a persistent condition (never a fixed short sleep)."""
    import time

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition did not become true within {timeout}s: {last!r}")


def wait_run_state(dbname: str, run_id: str, states: set[str], *, timeout: float = 60.0):
    return observations.wait_run_state(dbname, run_id, states, timeout=timeout)


def assert_slot_consistent(dbname: str) -> None:
    """Every live allocation is counted and every released one is not."""
    counters = {
        (row["scope"], row["dimension"]): row
        for row in st.sql_rows(
            dbname,
            "SELECT scope, dimension, used, capacity FROM platform.quota_counters",
        )
    }
    live: dict[tuple[str, str], int] = {}
    for row in allocation_rows(dbname):
        if row["released_at"] is None:
            key = (row["scope"], row["dimension"])
            live[key] = live.get(key, 0) + 1
    for key, counter in counters.items():
        assert counter["used"] == live.get(key, 0), (key, counter, live)
        assert 0 <= counter["used"] <= counter["capacity"], counter


__all__ = [name for name in dir() if not name.startswith("_")]
