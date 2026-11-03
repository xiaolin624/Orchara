"""F06/T046 acceptance: shared decimal budget reservation and actual settlement.

Every test is the real stack: PostgreSQL migrated through migration 0028, the
compiled API, the real mTLS capability endpoint, the real sealed manifest and
object store. The provider is the documented dev stub; the budget is a real
``platform.usage_ledger`` and never a number computed in the page or the test.

Covered:

* two truly concurrent calls behind a PostgreSQL row-lock gate cannot both take
  the last reservation (one 200, one typed 429 ``budget_exhausted``);
* actual usage settles the price version, releases the unused reserve and the
  per-attempt projection matches the ledger;
* a missing usage object keeps the conservative reservation (``usage_missing``)
  until a maintenance reconciliation charges/releases it, with no negative
  amount anywhere;
* retries and a compatible fallback charge the same parent budget, every failed
  attempt releasing its own hold;
* a replay/resume does not reserve or charge twice;
* a budget refusal is a typed platform failure that creates no provider attempt
  and keeps already-confirmed artifacts;
* large price/token quantities stay exact numeric(20,8) values.
"""

from __future__ import annotations

from decimal import Decimal

from support import t041, t045, t046


def priced_config(**overrides) -> dict:
    config = t045.compatible_config(**overrides)
    config["price"] = {
        "currency": "USD",
        "input_per_million": "1",
        "output_per_million": "2",
    }
    return config


def test_concurrent_calls_cannot_exceed_the_parent_budget(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    t046.configure_golden(
        f06_stack.dbname, model_id="stub-usage-missing", config=priced_config()
    )
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    params = t041.model_params()
    reservation = t046.expected_reservation(
        params, input_per_million="1", output_per_million="2", max_output_tokens=4096
    )
    t046.set_budget(f06_stack.dbname, run["id"], str(reservation))

    operation_ids = [t041.new_uuid(), t041.new_uuid()]
    responses = t046.race_invocations(f06_stack, lease, operation_ids=operation_ids, params=params)

    statuses = sorted(response.status for response in responses)
    assert statuses == [200, 429], [(response.status, response.text) for response in responses]
    winner = next(response for response in responses if response.status == 200)
    refused = next(response for response in responses if response.status == 429)
    assert winner.json["state"] == "succeeded", winner.text
    error = refused.json["error"]
    assert error["code"] == "budget_exhausted", refused.text
    assert Decimal(error["details"]["remaining_amount"]) == Decimal("0.00000000"), error
    assert error["details"]["currency"] == "USD", error
    assert Decimal(error["details"]["required_amount"]) == reservation, error

    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    reserves = [row for row in rows if row["kind"] == "reserve"]
    assert len(reserves) == 1, rows
    assert Decimal(reserves[0]["amount"]) == reservation, reserves
    assert reserves[0]["price_version"].startswith("price-v1:" + t041.GOLDEN_MODEL_ID), reserves
    debits = t046.ledger_debits(f06_stack.dbname, run["id"])
    assert Decimal(debits["reserved"]) == reservation, debits
    assert Decimal(debits["charged"]) == 0, debits
    assert Decimal(debits["outstanding"]) == reservation, debits

    # The winner is honest about the missing usage: the reserve is retained,
    # not charged. The loser never created a provider attempt.
    winner_operation = winner.json["operation_id"]
    attempt = t046.attempt_row(f06_stack.dbname, winner_operation, 1)
    assert attempt["settlement_state"] == "usage_missing", attempt
    assert attempt["charged_amount"] is None, attempt
    assert "usage" not in winner.json.get("data", {}), winner.json
    failed_rows = [
        item for item in t041.ledger_items(owner.creator, run["id"])
        if item["state"] == "failed"
    ]
    assert len(failed_rows) == 1, failed_rows
    assert failed_rows[0]["error_code"] == "budget_exhausted", failed_rows
    assert t041.attempt_count(f06_stack.dbname, failed_rows[0]["id"]) == 0, failed_rows

    facts = t046.budget_facts(owner, run["id"])
    assert Decimal(facts["budget_limit"]) == reservation, facts
    assert Decimal(facts["charged_amount"]) == 0, facts
    assert Decimal(facts["reserved_amount"]) == reservation, facts
    assert Decimal(facts["remaining_amount"]) == 0, facts
    assert facts["overrun"] is False, facts


def test_actual_usage_settles_price_version_and_releases_the_reserve(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    t046.configure_golden(f06_stack.dbname, config=priced_config())
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    params = t041.model_params()
    reservation = t046.expected_reservation(
        params, input_per_million="1", output_per_million="2"
    )

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease,
                           operation_id=operation_id, params=params)
    assert response.status == 200, response.text
    assert response.json["state"] == "succeeded", response.text
    usage = response.json["data"]["usage"]
    charge = t046.expected_charge(
        usage["input_tokens"], usage["output_tokens"],
        input_per_million="1", output_per_million="2",
    )

    attempt = t046.attempt_row(f06_stack.dbname, operation_id, 1)
    assert attempt["settlement_state"] == "settled", attempt
    assert Decimal(attempt["reserved_amount"]) == reservation, attempt
    assert Decimal(attempt["charged_amount"]) == charge, attempt
    assert int(attempt["tokens_in"]) == usage["input_tokens"], attempt
    assert int(attempt["tokens_out"]) == usage["output_tokens"], attempt
    assert attempt["price_version"] == (
        f"price-v1:{t041.GOLDEN_MODEL_ID}:in=1:out=2:USD"
    ), attempt
    assert attempt["currency"] == "USD", attempt

    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    kinds = sorted(row["kind"] for row in rows)
    assert kinds == ["charge", "release", "reserve"], rows
    reserve = next(row for row in rows if row["kind"] == "reserve")
    release = next(row for row in rows if row["kind"] == "release")
    charged = next(row for row in rows if row["kind"] == "charge")
    assert Decimal(reserve["amount"]) == reservation, rows
    assert Decimal(release["amount"]) == reservation, rows
    assert Decimal(charged["amount"]) == charge, rows
    assert charged["tokens_in"] == usage["input_tokens"], rows
    assert charged["tokens_out"] == usage["output_tokens"], rows
    assert release["price_version"] == attempt["price_version"], rows
    assert charged["price_version"] == attempt["price_version"], rows

    debits = t046.ledger_debits(f06_stack.dbname, run["id"])
    assert Decimal(debits["charged"]) == charge, debits
    assert Decimal(debits["outstanding"]) == 0, debits
    facts = t046.budget_facts(owner, run["id"])
    assert Decimal(facts["charged_amount"]) == charge, facts
    assert Decimal(facts["reserved_amount"]) == 0, facts
    assert Decimal(facts["remaining_amount"]) == Decimal("1.00") - charge, facts
    assert facts["overrun"] is False, facts


def test_missing_usage_is_retained_and_later_reconciled(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    t046.configure_golden(
        f06_stack.dbname, model_id="stub-usage-missing", config=priced_config()
    )
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    params = t041.model_params()
    reservation = t046.expected_reservation(
        params, input_per_million="1", output_per_million="2"
    )

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease,
                           operation_id=operation_id, params=params)
    assert response.status == 200, response.text
    assert response.json["state"] == "succeeded", response.text
    assert "usage" not in response.json["data"], response.json

    attempt = t046.attempt_row(f06_stack.dbname, operation_id, 1)
    assert attempt["settlement_state"] == "usage_missing", attempt
    assert attempt["charged_amount"] is None, attempt
    assert attempt["error_code"] is None, attempt
    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    assert [row["kind"] for row in rows] == ["reserve"], rows
    facts = t046.budget_facts(owner, run["id"])
    assert Decimal(facts["reserved_amount"]) == reservation, facts
    assert Decimal(facts["charged_amount"]) == 0, facts

    # The worker capacity cannot invent a cost; only maintenance reconciles.
    forbidden = t041.worker_client(f06_stack).post(
        f"/internal/v1/runs/{run['id']}/capability-settlements",
        {"operation_id": operation_id, "attempt_no": 1, "amount": "0.00000001"},
    )
    assert forbidden.status == 403, forbidden.text

    actual = Decimal("0.00250000")
    settled = t046.reconcile(
        f06_stack, run["id"], operation_id=operation_id, attempt_no=1,
        amount=str(actual), tokens_in=120, tokens_out=340,
    )
    assert settled.status == 200, settled.text
    assert settled.json["state"] == "settled", settled.text
    assert Decimal(settled.json["charged_amount"]) == actual, settled.text
    assert settled.json["already_settled"] is False, settled.text

    attempt = t046.attempt_row(f06_stack.dbname, operation_id, 1)
    assert attempt["settlement_state"] == "settled", attempt
    assert Decimal(attempt["charged_amount"]) == actual, attempt
    assert int(attempt["tokens_in"]) == 120 and int(attempt["tokens_out"]) == 340, attempt

    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    kinds = sorted(row["kind"] for row in rows)
    assert kinds == ["charge", "release", "reserve"], rows
    release = next(row for row in rows if row["kind"] == "release")
    charged = next(row for row in rows if row["kind"] == "charge")
    assert Decimal(release["amount"]) == reservation, rows
    assert Decimal(charged["amount"]) == actual, rows
    for row in rows:
        assert Decimal(row["amount"]) >= 0, row
    debits = t046.ledger_debits(f06_stack.dbname, run["id"])
    assert Decimal(debits["outstanding"]) == 0, debits
    assert Decimal(debits["charged"]) == actual, debits

    # Idempotent replay of the same reconciliation changes nothing.
    again = t046.reconcile(
        f06_stack, run["id"], operation_id=operation_id, attempt_no=1,
        amount=str(actual), tokens_in=120, tokens_out=340,
    )
    assert again.status == 200, again.text
    assert again.json["already_settled"] is True, again.text
    assert len(t046.ledger_rows(f06_stack.dbname, run["id"])) == 3


def test_retries_and_fallback_share_the_same_parent_budget(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    fallback_id = t045.seed_profile(
        f06_stack.dbname,
        owner.creator.principal_id,
        model_id="stub-model",
        config=priced_config(),
    )
    t046.configure_golden(
        f06_stack.dbname,
        model_id="stub-unavailable-primary",
        config=priced_config(fallback_profile_version_ids=[fallback_id]),
    )
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease,
                           operation_id=operation_id, params=t041.model_params())
    assert response.status == 200, response.text
    assert response.json["data"]["fallback"]["profile_version_id"] == fallback_id, response.json
    usage = response.json["data"]["usage"]
    charge = t046.expected_charge(
        usage["input_tokens"], usage["output_tokens"],
        input_per_million="1", output_per_million="2",
    )

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert [row["state"] for row in attempts] == ["failed", "failed", "failed", "succeeded"], attempts
    for failed in attempts[:3]:
        assert failed["settlement_state"] == "released", failed
        assert failed["charged_amount"] is None, failed
    succeeded = attempts[3]
    assert succeeded["settlement_state"] == "settled", succeeded
    assert Decimal(succeeded["charged_amount"]) == charge, succeeded

    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    assert {row["budget_root_run_id"] for row in rows} == {run["id"]}, rows
    assert [row["kind"] for row in rows].count("reserve") == 4, rows
    assert [row["kind"] for row in rows].count("release") == 4, rows
    assert [row["kind"] for row in rows].count("charge") == 1, rows
    charged = [row for row in rows if row["kind"] == "charge"][0]
    assert Decimal(charged["amount"]) == charge, rows
    # Failed retries never spend: only the fallback's actual usage remains.
    facts = t046.budget_facts(owner, run["id"])
    assert Decimal(facts["charged_amount"]) == charge, facts
    assert Decimal(facts["reserved_amount"]) == 0, facts


def test_replay_does_not_reserve_or_charge_twice(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    t046.configure_golden(f06_stack.dbname, config=priced_config())
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    operation_id = t041.new_uuid()
    params = t041.model_params()

    first = t041.invoke(t041.worker_client(f06_stack), lease,
                        operation_id=operation_id, params=params)
    assert first.status == 200, first.text
    replay = t041.invoke(t041.worker_client(f06_stack), lease,
                         operation_id=operation_id, params=params)
    assert replay.status == 200, replay.text
    assert replay.json["state"] == "succeeded", replay.text
    assert replay.json["receipt_artifact_id"] == first.json["receipt_artifact_id"], replay.text

    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    assert [row["kind"] for row in rows].count("reserve") == 1, rows
    assert [row["kind"] for row in rows].count("charge") == 1, rows
    assert t041.attempt_count(f06_stack.dbname, operation_id) == 1
    facts = t046.budget_facts(owner, run["id"])
    usage = first.json["data"]["usage"]
    charge = t046.expected_charge(
        usage["input_tokens"], usage["output_tokens"],
        input_per_million="1", output_per_million="2",
    )
    assert Decimal(facts["charged_amount"]) == charge, facts
    assert Decimal(facts["reserved_amount"]) == 0, facts


def test_budget_refusal_keeps_already_confirmed_artifacts(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    t046.configure_golden(f06_stack.dbname, config=priced_config())
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    confirmed = t041.invoke(t041.worker_client(f06_stack), lease,
                            operation_id=t041.new_uuid(), params=t041.model_params())
    assert confirmed.status == 200, confirmed.text
    confirmed_receipt = confirmed.json["receipt_artifact_id"]
    facts = t046.budget_facts(owner, run["id"])
    charged = Decimal(facts["charged_amount"])
    # Exactly the charged amount remains: no positive reservation can fit.
    t046.set_budget(f06_stack.dbname, run["id"], str(charged))
    params = t041.model_params(messages=[{"role": "user", "content": "Second call."}])
    refused = t041.invoke(t041.worker_client(f06_stack), lease,
                          operation_id=t041.new_uuid(), logical_key="call-2", params=params)
    assert refused.status == 429, refused.text
    assert refused.json["error"]["code"] == "budget_exhausted", refused.text
    assert Decimal(refused.json["error"]["details"]["remaining_amount"]) == 0, refused.text

    # The confirmed artifact and its operation are untouched.
    artifact = t041.artifact_row(f06_stack.dbname, confirmed_receipt)
    assert artifact["state"] == "ready", artifact
    items = t041.ledger_items(owner.creator, run["id"])
    succeeded = [item for item in items if item["state"] == "succeeded"]
    failed = [item for item in items if item["state"] == "failed"]
    assert len(succeeded) == 1 and succeeded[0]["receipt_artifact_id"] == confirmed_receipt, items
    assert len(failed) == 1 and failed[0]["error_code"] == "budget_exhausted", items
    assert t041.attempt_count(f06_stack.dbname, failed[0]["id"]) == 0, failed
    # No provider attempt and no reserve row for the refused operation.
    refused_rows = [row for row in t046.ledger_rows(f06_stack.dbname, run["id"])
                    if row["operation_id"] == failed[0]["id"]]
    assert refused_rows == [], refused_rows


def test_large_price_and_token_counts_stay_exact(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    huge_price = "999999999999.99999999"
    config = t045.compatible_config(max_output_tokens=1000000)
    config["price"] = {
        "currency": "USD",
        "input_per_million": "0",
        "output_per_million": huge_price,
    }
    t046.configure_golden(f06_stack.dbname, config=config)
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    params = t041.model_params()
    reservation = t046.expected_reservation(
        params, input_per_million="0", output_per_million=huge_price,
        max_output_tokens=1000000,
    )
    assert str(reservation) == huge_price, reservation
    t046.set_budget(f06_stack.dbname, run["id"], str(reservation))

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease,
                           operation_id=operation_id, params=params)
    assert response.status == 200, response.text
    usage = response.json["data"]["usage"]
    charge = t046.expected_charge(
        usage["input_tokens"], usage["output_tokens"],
        input_per_million="0", output_per_million=huge_price,
    )
    attempt = t046.attempt_row(f06_stack.dbname, operation_id, 1)
    assert Decimal(attempt["reserved_amount"]) == Decimal(huge_price), attempt
    assert Decimal(attempt["charged_amount"]) == charge, attempt
    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    reserve = next(row for row in rows if row["kind"] == "reserve")
    charged = next(row for row in rows if row["kind"] == "charge")
    assert reserve["amount"] == huge_price, reserve
    assert Decimal(charged["amount"]) == charge, charged
    facts = t046.budget_facts(owner, run["id"])
    assert Decimal(facts["charged_amount"]) == charge, facts
    assert Decimal(facts["remaining_amount"]) == Decimal(huge_price) - charge, facts
