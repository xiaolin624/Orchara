"""F07/T080 acceptance: parallel child calls consume one shared parent budget.

Every case runs the real stack: PostgreSQL 16.4 migrated by the compiled
``cmd/admin`` chain, the compiled API behind real HTTP/mTLS, real ``spawnChild``
calls, real supervisor executions and real ``platform.usage_ledger`` rows. The
concurrency barrier is a PostgreSQL row lock on the budget root (or on the parent
Run for spawn admission) -- never a wall-clock sleep.

Covered:

* two *spawned* children racing the last reservation of one parent allowance:
  exactly one fits, the loser is a typed 429 ``budget_exhausted`` with no reserve
  row and no partial settlement; money stays ``numeric(20,8)``;
* barrier-synchronised spawns consume the parent's child-call allowance
  atomically (exactly N create a Run, the rest are 429 ``child_calls_exceeded``,
  no orphan Run/artifact row);
* depth is measured from the real ancestor chain and checked against the
  delegating Run's frozen policy;
* a child whose model reservation cannot fit fails with ``budget_exhausted`` and
  the required-child rule converges the parent with the same reason;
* recovery (real ``orchara-admin runs recover``) and a fallback model never reset
  or double-charge the consumed allowance.
"""

from __future__ import annotations

import json
from decimal import Decimal

from support import t025, t029, t032, t038, t041, t045, t046, t075, t078, t080
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    t029_stack,
)


# ---------------------------------------------------------------------------
# 1. cost: concurrent child reservations cannot exceed the parent allowance
# ---------------------------------------------------------------------------
def test_concurrent_child_model_calls_cannot_exceed_the_shared_allowance(t029_stack) -> None:
    _admin, creator = t075.creator(t029_stack)
    model_id = t080.seed_priced_golden(t029_stack, creator)
    child_version = t080.model_child_version(t029_stack, creator, model_id=model_id)
    _application, parent = t080.debug_parent(
        t029_stack, creator, child_version["id"], output_key="report"
    )
    dbname = t029_stack.dbname

    # Money is decimal only: both columns are numeric(20,8), never a float type.
    columns = t080.money_columns(dbname)
    for key in (("usage_ledger", "amount"), ("runs", "budget_limit")):
        column = columns[key]
        assert column["data_type"] == "numeric", column
        assert column["udt_name"] == "numeric", column
        assert (int(column["numeric_precision"]), int(column["numeric_scale"])) == (20, 8), column

    t080.set_capacity(t029_stack, 8)
    parent_lease = t080.claim(t029_stack, run_id=parent["id"])
    params = t041.model_params()
    required = t080.reservation(params)
    # One allowance: the parent's budget_limit is the only budget the tree has.
    t080.set_parent_budget(dbname, parent["id"], required)

    spawned = [
        t078.spawn(
            t029_stack, parent["id"], attempt_token=parent_lease["attempt_token"],
            logical_key=f"child-{index}", application_version_id=child_version["id"],
            input={"source": f"source-{index}"},
        )
        for index in range(2)
    ]
    assert [response.status for response in spawned] == [202, 202], [r.text for r in spawned]
    child_ids = [response.json["id"] for response in spawned]
    for child_id in child_ids:
        # The child's frozen allowance is the parent's shared allowance, never a
        # second budget of its own.
        assert t080.run_budget(dbname, child_id) == t080.run_budget(dbname, parent["id"])

    leases = []
    for _ in child_ids:
        lease = t080.claim(t029_stack)
        leases.append(lease)
    assert {lease["run_id"] for lease in leases} == set(child_ids), leases
    operation_ids = [t041.new_uuid(), t041.new_uuid()]
    responses = t080.race_child_invocations(
        t029_stack, leases, params=params, operation_ids=operation_ids,
        gate_run_id=parent["id"],
    )
    statuses = sorted(response.status for response in responses)
    assert statuses == [200, 429], [(r.status, r.text) for r in responses]
    winner = next(response for response in responses if response.status == 200)
    refused = next(response for response in responses if response.status == 429)
    error = refused.json["error"]
    assert error["code"] == "budget_exhausted", refused.text
    assert error["details"]["currency"] == "USD", error
    assert Decimal(error["details"]["required_amount"]) == required, error
    # The loser re-read the tree under the root lock after the winner committed:
    # whatever remains (zero, or the winner's already-settled charge) is short of
    # one more reservation, and the loser wrote no ledger row.
    assert Decimal("0") <= Decimal(error["details"]["remaining_amount"]) < required, error

    winner_lease = leases[[response is winner for response in responses].index(True)]
    loser_lease = leases[[response is refused for response in responses].index(True)]
    winner_operation = winner.json["operation_id"]
    refused_operation = next(
        row for row in t041.operation_rows(dbname, loser_lease["run_id"])
    )

    rows = t080.ledger_rows_at_root(dbname, parent["id"])
    assert {row["budget_root_run_id"] for row in rows} == {parent["id"]}, rows
    assert [row for row in rows if row["run_id"] == loser_lease["run_id"]] == [], rows
    reserves = [row for row in rows if row["kind"] == "reserve"]
    assert len(reserves) == 1, rows
    assert reserves[0]["run_id"] == winner_lease["run_id"], reserves
    assert Decimal(reserves[0]["amount"]) == required, reserves
    for row in rows:
        assert t080.AMOUNT_TEXT.fullmatch(row["amount"]), row
    totals = t080.ledger_totals_at_root(dbname, parent["id"])
    assert Decimal(totals["reserved"]) <= required, totals
    assert Decimal(totals["reserved"]) == required, totals

    # The refused child's operation is a typed platform failure with no attempt:
    # no partial reservation leaked.
    assert refused_operation["state"] == "failed", refused_operation
    assert refused_operation["last_error_code"] == "budget_exhausted", refused_operation
    assert t041.attempt_count(dbname, refused_operation["id"]) == 0

    # The winning child settled its actual usage against the same root.
    usage = winner.json["data"]["usage"]
    charge = t080.expected_charge(usage["input_tokens"], usage["output_tokens"])
    charged = [row for row in rows if row["kind"] == "charge"]
    assert len(charged) == 1 and Decimal(charged[0]["amount"]) == charge, rows
    settled = t046.attempt_row(dbname, winner_operation, 1)
    assert settled["settlement_state"] == "settled", settled
    assert Decimal(settled["charged_amount"]) == charge, settled

    facts = t041.ledger_budget(creator, parent["id"])
    assert Decimal(facts["budget_limit"]) == required, facts
    assert Decimal(facts["charged_amount"]) == charge, facts
    assert Decimal(facts["remaining_amount"]) == required - charge, facts


# ---------------------------------------------------------------------------
# 2. count: barrier-synchronised spawns consume the child-call allowance
# ---------------------------------------------------------------------------
def test_concurrent_spawns_consume_the_child_call_allowance_atomically(t029_stack) -> None:
    _admin, creator = t075.creator(t029_stack)
    child_version = t078.create_child_version(t029_stack, creator)
    _application, parent = t080.debug_parent(
        t029_stack, creator, child_version["id"], max_child_calls=2
    )
    dbname = t029_stack.dbname
    t080.set_capacity(t029_stack, 8)
    lease = t080.claim(t029_stack, run_id=parent["id"])

    runs_before = t078.run_count(dbname)
    inputs_before = t080.run_input_artifact_count(dbname)
    logical_keys = ["concurrent-1", "concurrent-2", "concurrent-3"]
    responses = t080.race_spawns(
        t029_stack, parent["id"], attempt_token=lease["attempt_token"],
        application_version_id=child_version["id"], logical_keys=logical_keys,
    )

    statuses = sorted(response.status for response in responses)
    assert statuses == [202, 202, 429], [(r.status, r.text) for r in responses]
    winners = [response for response in responses if response.status == 202]
    loser = next(response for response in responses if response.status == 429)
    error = loser.json["error"]
    assert error["code"] == "child_calls_exceeded", loser.text
    assert error["details"] == {"used": 2, "max_child_calls": 2}, error

    # Exactly two child Runs and two receipts exist; the third request left no
    # Run, job, receipt or sealed-input artifact behind.
    receipts = t078.run_children_rows(dbname, parent["id"])
    assert len(receipts) == 2, receipts
    assert t078.child_count(dbname, parent["id"]) == 2
    assert t078.run_count(dbname) == runs_before + 2
    assert t080.run_input_artifact_count(dbname) == inputs_before + 2
    loser_key = logical_keys[responses.index(loser)]
    assert all(receipt["parent_invocation_key"] != loser_key for receipt in receipts), receipts
    assert {receipt["parent_invocation_key"] for receipt in receipts} == set(
        logical_keys[index] for index in range(len(logical_keys)) if responses[index] is not loser
    ), receipts

    # Every spawned child froze the same parent allowance and depth 1.
    parent_budget = t080.run_budget(dbname, parent["id"])
    for receipt in receipts:
        assert receipt["depth"] == 1, receipt
        assert t080.run_budget(dbname, receipt["child_run_id"]) == parent_budget, receipt
    # Spawn admission writes no cost row: money is only reserved at a model call.
    assert t080.ledger_rows_at_root(dbname, parent["id"]) == []


# ---------------------------------------------------------------------------
# 3. depth: measured from the real ancestor chain, bounded by the parent policy
# ---------------------------------------------------------------------------
def test_depth_is_measured_from_the_real_ancestor_chain(t029_stack) -> None:
    _admin, creator = t075.creator(t029_stack)
    grandchild_version = t078.create_child_version(t029_stack, creator)
    # The child may delegate one level (closure depth 1) and declares that bound.
    child_version = t078.create_child_version(
        t029_stack, creator,
        definition=t080.parent_definition(grandchild_version["id"], max_child_depth=1),
    )
    # The root's policy allows the declared two-generation closure.
    _application, root = t080.debug_parent(
        t029_stack, creator, child_version["id"], max_child_depth=2
    )
    dbname = t029_stack.dbname
    t080.set_capacity(t029_stack, 8)
    root_lease = t080.claim(t029_stack, run_id=root["id"])

    first = t078.spawn(
        t029_stack, root["id"], attempt_token=root_lease["attempt_token"],
        logical_key="call_app:child", application_version_id=child_version["id"],
        input={"source": "level-one"},
    )
    assert first.status == 202, first.text
    child_run_id = first.json["id"]
    receipt = t078.run_children_row(dbname, root["id"], "call_app:child")
    assert receipt["depth"] == 1, receipt
    assert t080.run_budget(dbname, child_run_id) == t080.run_budget(dbname, root["id"])

    # Execute the second delegation from the real child Run: its depth is two
    # generations below the root, which is beyond its own frozen bound of one.
    child_lease = t080.claim(t029_stack, run_id=child_run_id)
    runs_before = t078.run_count(dbname)
    refused = t078.spawn(
        t029_stack, child_run_id, attempt_token=child_lease["attempt_token"],
        logical_key="call_app:child", application_version_id=grandchild_version["id"],
        input={"source": "level-two"},
    )
    assert refused.status == 422, refused.text
    error = refused.json["error"]
    assert error["code"] == "child_depth_exceeded", refused.text
    assert error["details"] == {"depth": 2, "max_child_depth": 1}, error
    # The refusal is atomic: no child Run, receipt or input artifact was written.
    assert t078.run_children_rows(dbname, child_run_id) == []
    assert t078.run_count(dbname) == runs_before
    assert t080.ledger_rows_at_root(dbname, root["id"]) == []


# ---------------------------------------------------------------------------
# 4. over-limit reason returns to the parent (required-child convergence)
# ---------------------------------------------------------------------------
def test_a_parent_with_no_remaining_allowance_refuses_a_new_child(t029_stack) -> None:
    _admin, creator = t075.creator(t029_stack)
    model_id = t080.seed_priced_golden(t029_stack, creator)
    child_version = t080.agent_child_version(t029_stack, creator, model_id=model_id)
    _application, parent = t080.debug_parent(
        t029_stack, creator, child_version["id"], output_key="text"
    )
    dbname = t029_stack.dbname
    t080.set_capacity(t029_stack, 8)
    lease = t080.claim(t029_stack, run_id=parent["id"])
    t080.set_parent_budget(dbname, parent["id"], "0")

    refused = t078.spawn(
        t029_stack, parent["id"], attempt_token=lease["attempt_token"],
        logical_key="call_app:child", application_version_id=child_version["id"],
        input={"source": "unaffordable"},
    )
    assert refused.status == 429, refused.text
    error = refused.json["error"]
    assert error["code"] == "budget_exhausted", refused.text
    assert Decimal(error["details"]["remaining_amount"]) == 0, error
    assert error["details"]["currency"] == "USD", error
    assert t078.run_children_rows(dbname, parent["id"]) == []
    assert t080.ledger_rows_at_root(dbname, parent["id"]) == []


def test_an_unaffordable_child_returns_budget_exhausted_to_the_parent(t029_stack) -> None:
    _admin, creator = t075.creator(t029_stack)
    model_id = t080.seed_priced_golden(t029_stack, creator)
    child_version = t080.agent_child_version(t029_stack, creator, model_id=model_id)
    _application, root = t080.debug_parent(
        t029_stack, creator, child_version["id"], output_key="text"
    )
    dbname = t029_stack.dbname
    t080.set_capacity(t029_stack, 8)

    # A positive-but-tiny allowance admits the delegation; the child's model
    # reservation is then refused by the same ledger and converges the parent.
    t080.set_parent_budget(dbname, root["id"], t080.ONE_UNIT)
    first, _ = t078.execute_once(t029_stack)
    assert first["status"] == "executed" and first["run_id"] == root["id"], first
    assert first["outcome"] == "waiting_children", first
    waiting = t078.waiting_row(dbname, root["id"])
    assert waiting["kind"] == "children", waiting
    child_run_id = waiting["checkpoint_id"].split(":", 1)[1]
    child_budget = t080.run_budget(dbname, child_run_id)
    assert child_budget == ("0.00000001", "USD"), child_budget

    second, _ = t078.execute_once(t029_stack)
    assert second["run_id"] == child_run_id and second["status"] == "executed", second
    assert second["outcome"] == "failed", second
    assert second["error_code"] == "budget_exhausted", second
    child_row = t078.wait_run_state(dbname, child_run_id, {"failed"})
    assert child_row["error_code"] == "budget_exhausted", child_row
    assert child_row["output_artifact_id"] is None, child_row
    # The refused reservation left no row at all, and the child stays at depth 1.
    assert t080.ledger_rows_at_root(dbname, root["id"]) == []
    assert t078.run_children_rows(dbname, root["id"])[0]["depth"] == 1
    child_statuses = t032.status_events(dbname, child_run_id)
    assert child_statuses[-1][2] == {
        "state": "failed", "error_code": "budget_exhausted",
    }, child_statuses[-1][2]

    # The required child's failure travels back to the parent Run: replay does
    # not re-create or re-charge anything, it reads the child's reason.
    resolved = t078.resolve_children_wait(
        t029_stack, root["id"], child_run_id, outcome="failed"
    )
    assert resolved.status == 200, resolved.text
    t078.wait_for_resume_job(dbname, root["id"])
    third, _ = t078.execute_once(t029_stack)
    assert third["status"] == "executed" and third["run_id"] == root["id"], third
    assert third["outcome"] == "failed", third
    assert third["error_code"] == "budget_exhausted", third
    parent_row = t078.wait_run_state(dbname, root["id"], {"failed"})
    assert parent_row["error_code"] == "budget_exhausted", parent_row
    parent_statuses = t032.status_events(dbname, root["id"])
    assert parent_statuses[-1][2] == {
        "state": "failed", "error_code": "budget_exhausted",
    }, parent_statuses[-1][2]
    # No second child was created by the resumed attempt.
    assert len(t078.run_children_rows(dbname, root["id"])) == 1


# ---------------------------------------------------------------------------
# 5. recovery: the consumed allowance is preserved, never reset
# ---------------------------------------------------------------------------
def test_recovery_preserves_the_consumed_allowance(t029_stack, tmp_path) -> None:
    _admin, creator = t075.creator(t029_stack)
    model_id = t080.seed_priced_golden(t029_stack, creator)
    child_version = t080.model_child_version(t029_stack, creator, model_id=model_id)
    _application, parent = t080.debug_parent(
        t029_stack, creator, child_version["id"], output_key="report"
    )
    dbname = t029_stack.dbname
    t080.set_capacity(t029_stack, 8)
    parent_lease = t080.claim(t029_stack, run_id=parent["id"])
    params = t041.model_params()
    required = t080.reservation(params)
    t080.set_parent_budget(dbname, parent["id"], required)

    spawned = t078.spawn(
        t029_stack, parent["id"], attempt_token=parent_lease["attempt_token"],
        logical_key="spend-once", application_version_id=child_version["id"],
        input={"source": "recovered-source"},
    )
    assert spawned.status == 202, spawned.text
    child_run_id = spawned.json["id"]
    child_lease = t080.claim(t029_stack, run_id=child_run_id, unit="t080-budget")
    first = t041.invoke(
        t041.worker_client(t029_stack), child_lease,
        operation_id=t041.new_uuid(), params=params,
    )
    assert first.status == 200, first.text
    usage = first.json["data"]["usage"]
    charge = t080.expected_charge(usage["input_tokens"], usage["output_tokens"])

    rows_before = t080.ledger_rows_at_root(dbname, parent["id"])
    totals_before = t080.ledger_totals_at_root(dbname, parent["id"])
    assert {row["budget_root_run_id"] for row in rows_before} == {parent["id"]}, rows_before
    assert Decimal(totals_before["charged"]) == charge, totals_before
    assert Decimal(totals_before["outstanding"]) == 0, totals_before
    child_budget_before = t080.run_budget(dbname, child_run_id)

    # Real recovery: lease expiry -> real sweep -> real `orchara-admin runs recover`.
    t080.expire_and_park(t029_stack, child_lease)
    report = t038.recover_ok(
        t029_stack, child_run_id,
        evidence=t038.recovery_evidence("t080-budget"), directory=tmp_path,
    )
    assert report["state"] == "queued", report
    # Recovery restored execution, never money: the ledger is byte-for-byte the
    # same and the frozen allowance is unchanged.
    assert t080.ledger_rows_at_root(dbname, parent["id"]) == rows_before
    assert t080.ledger_totals_at_root(dbname, parent["id"]) == totals_before
    assert t080.run_budget(dbname, child_run_id) == child_budget_before
    # The parent Run -- the budget root the child draws from -- is untouched too.
    assert t080.run_budget(dbname, parent["id"]) == (str(required), "USD")
    recovered_facts = t041.ledger_budget(creator, parent["id"])
    assert Decimal(recovered_facts["remaining_amount"]) == required - charge, recovered_facts
    assert t038.run_status_states(dbname, child_run_id)[-1] == "queued", (
        t038.run_status_states(dbname, child_run_id)
    )

    resumed_lease = t080.claim(t029_stack, run_id=child_run_id)
    assert resumed_lease["attempt_token"] != child_lease["attempt_token"]
    # The spent allowance is gone: the next reservation of the same size is
    # refused, and the refusal writes nothing.
    refused = t041.invoke(
        t041.worker_client(t029_stack), resumed_lease,
        operation_id=t041.new_uuid(), logical_key="call-2", params=params,
    )
    assert refused.status == 429, refused.text
    assert refused.json["error"]["code"] == "budget_exhausted", refused.text
    assert Decimal(refused.json["error"]["details"]["remaining_amount"]) == required - charge
    assert t080.ledger_rows_at_root(dbname, parent["id"]) == rows_before
    totals_after = t080.ledger_totals_at_root(dbname, parent["id"])
    assert Decimal(totals_after["charged"]) == charge, totals_after


# ---------------------------------------------------------------------------
# 6. fallback: a backup model is not a new budget
# ---------------------------------------------------------------------------
def test_a_child_fallback_is_not_a_new_allowance(t029_stack) -> None:
    _admin, creator = t075.creator(t029_stack)
    dbname = t029_stack.dbname
    t025.seed_model_version(dbname, creator.principal_id, model_id=t025.GOLDEN_MODEL_ID)
    fallback_id = t045.seed_profile(
        dbname, creator.principal_id, model_id="stub-model", config=t080.priced_config()
    )
    t046.configure_golden(
        dbname, model_id="stub-unavailable-primary",
        config=t080.priced_config([fallback_id]),
    )
    child_version = t080.model_child_version(
        t029_stack, creator, model_id=t025.GOLDEN_MODEL_ID
    )
    _application, parent = t080.debug_parent(
        t029_stack, creator, child_version["id"], output_key="report"
    )
    t080.set_capacity(t029_stack, 8)
    parent_lease = t080.claim(t029_stack, run_id=parent["id"])
    params = t041.model_params()
    required = t080.reservation(params)
    t080.set_parent_budget(dbname, parent["id"], required)

    spawned = t078.spawn(
        t029_stack, parent["id"], attempt_token=parent_lease["attempt_token"],
        logical_key="fallback-once", application_version_id=child_version["id"],
        input={"source": "fallback-source"},
    )
    assert spawned.status == 202, spawned.text
    child_run_id = spawned.json["id"]
    child_lease = t080.claim(t029_stack, run_id=child_run_id)

    response = t041.invoke(
        t041.worker_client(t029_stack), child_lease,
        operation_id=t041.new_uuid(), params=params,
    )
    assert response.status == 200, response.text
    assert response.json["data"]["fallback"]["profile_version_id"] == fallback_id, response.json
    usage = response.json["data"]["usage"]
    charge = t080.expected_charge(usage["input_tokens"], usage["output_tokens"])

    rows = t080.ledger_rows_at_root(dbname, parent["id"])
    assert {row["budget_root_run_id"] for row in rows} == {parent["id"]}, rows
    assert {row["run_id"] for row in rows} == {child_run_id}, rows
    kinds = [row["kind"] for row in rows]
    # Each failed primary attempt released its own hold; only the fallback's
    # actual usage is charged, once, against the one parent allowance.
    assert kinds.count("reserve") == kinds.count("release") >= 2, rows
    assert kinds.count("charge") == 1, rows
    for row in rows:
        assert t080.AMOUNT_TEXT.fullmatch(row["amount"]), row
    totals = t080.ledger_totals_at_root(dbname, parent["id"])
    assert Decimal(totals["outstanding"]) == 0, totals
    assert Decimal(totals["charged"]) == charge, totals
    assert Decimal(totals["reserved"]) == required * (kinds.count("reserve")), totals
    facts = t041.ledger_budget(creator, parent["id"])
    assert Decimal(facts["charged_amount"]) == charge, facts
    assert Decimal(facts["remaining_amount"]) == required - charge, facts
