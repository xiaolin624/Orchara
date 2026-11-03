"""T029 acceptance: concurrent claims can never double a run or over-allocate.

Every claim is a real mTLS request against the compiled API and the real
PostgreSQL cluster. The tests start ten claims behind a barrier (so they actually
compete) and then read the database invariants: one live attempt per run, one
allocation per job, `used` never above capacity.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from support import stack as st
from support import t029
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)

CLAIMERS = 10


def parallel_claims(t029_stack, count: int = CLAIMERS) -> list:
    barrier = threading.Barrier(count)

    def one(index: int):
        client = t029.execution_client(t029_stack, "supervisor")
        barrier.wait(timeout=60)
        return client.claim_job(
            worker_id="t029-racer-%d" % index,
            execution_unit_id="unit-%d" % index,
        )

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(one, index) for index in range(count)]
        return [future.result(timeout=180) for future in futures]


def assert_statuses(responses: list, granted: int) -> list[dict]:
    statuses = [response.status for response in responses]
    assert statuses.count(200) == granted, statuses
    assert statuses.count(204) == CLAIMERS - granted, statuses
    assert set(statuses) == {200, 204}, statuses
    leases = []
    for response in responses:
        if response.status == 200:
            t029.assert_lease(response.json)
            leases.append(response.json)
        else:
            assert response.raw == b"", response.raw
    return leases


def test_ten_parallel_claims_with_one_slot_grant_exactly_one_lease(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    first_run = t029.queued_run(t029_stack, owner)
    second_run = t029.queued_run(t029_stack, owner)
    t029.set_quota_capacity(t029_stack.dbname, 1)

    leases = assert_statuses(parallel_claims(t029_stack), granted=1)
    lease = leases[0]

    assert t029.attempt_count(t029_stack.dbname) == 1
    attempts = t029.attempts_for_run(t029_stack.dbname, lease["run_id"])
    assert len(attempts) == 1, attempts
    assert attempts[0]["token"] == lease["attempt_token"]

    runs = {
        first_run["id"]: t029.run_state_row(t029_stack.dbname, first_run["id"]),
        second_run["id"]: t029.run_state_row(t029_stack.dbname, second_run["id"]),
    }
    states = {run_id: row["state"] for run_id, row in runs.items()}
    assert sorted(states.values()) == ["queued", "running"], states
    assert runs[lease["run_id"]]["state"] == "running"
    assert runs[lease["run_id"]]["revision"] == 2

    for run in (first_run, second_run):
        jobs = t029.jobs_for_run(t029_stack.dbname, run["id"])
        assert len(jobs) == 1, jobs
        job = t029.job_row(t029_stack.dbname, jobs[0]["id"])
        if run["id"] == lease["run_id"]:
            assert job["state"] == "leased", job
            assert job["current_attempt_token"] == lease["attempt_token"], job
        else:
            assert job["state"] == "ready", job
            assert job["current_attempt_token"] is None, job
            assert t029.attempts_for_run(t029_stack.dbname, run["id"]) == []

    assert t029.quota_counter(t029_stack.dbname)["used"] == 1
    allocations = t029.quota_allocations(t029_stack.dbname)
    assert len(allocations) == 1, allocations
    assert allocations[0]["holder_key"] == "job:" + lease["job_id"]
    assert allocations[0]["released_at"] is None


def test_two_slots_serve_two_runs_and_never_the_same_run_twice(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    first_run = t029.queued_run(t029_stack, owner)
    second_run = t029.queued_run(t029_stack, owner)
    t029.set_quota_capacity(t029_stack.dbname, 2)

    leases = assert_statuses(parallel_claims(t029_stack), granted=2)
    assert len({lease["run_id"] for lease in leases}) == 2, leases
    assert {lease["run_id"] for lease in leases} == {first_run["id"], second_run["id"]}

    assert t029.attempt_count(t029_stack.dbname) == 2
    for run in (first_run, second_run):
        attempts = t029.attempts_for_run(t029_stack.dbname, run["id"])
        assert len(attempts) == 1, (run["id"], attempts)
        run_row = t029.run_state_row(t029_stack.dbname, run["id"])
        assert run_row["state"] == "running" and run_row["phase"] == "executing", run_row
        jobs = t029.jobs_for_run(t029_stack.dbname, run["id"])
        assert t029.job_row(t029_stack.dbname, jobs[0]["id"])["state"] == "leased"

    assert t029.quota_counter(t029_stack.dbname)["used"] == 2
    assert len(t029.quota_allocations(t029_stack.dbname)) == 2


def test_ten_parallel_claims_for_one_run_produce_one_attempt(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)
    t029.set_quota_capacity(t029_stack.dbname, 2)

    leases = assert_statuses(parallel_claims(t029_stack), granted=1)
    lease = leases[0]
    assert lease["run_id"] == run["id"]
    attempts = t029.attempts_for_run(t029_stack.dbname, run["id"])
    assert len(attempts) == 1, attempts
    assert t029.quota_counter(t029_stack.dbname)["used"] == 1
    assert len(t029.quota_allocations(t029_stack.dbname)) == 1
