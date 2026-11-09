"""C2.01 (#146): one model-attempt lifecycle for the sync primary and fallback.

Every test is the real stack: a throwaway PostgreSQL database migrated through the
real ``cmd/admin migrate`` chain, the compiled API behind real HTTP, the real
worker mTLS listener, the real sealed execution manifest of a debug run and the
real filesystem object store. The provider is the documented dev stub; a
profile's registered ``model_id`` selects a deterministic fault, and a second
real profile row is the compatible fallback frozen into the same manifest.

The tests pin the C2.01 behaviour observed through the existing capability
entries (there is no new endpoint, no new ledger and no new public type):

* an adapter-class availability fault (``ErrAdapterUnavailable``) is *not*
  retried on the primary. The fallback path already spends exactly one physical
  attempt on that class and so does the streaming path, so delivery mode does
  not change the attempt rules; a typed transient fault is still retried up to
  the existing three-attempt ceiling.
* a refusal after the operation row exists but before any physical call
  (actual-price config that cannot be reserved) closes the operation as failed
  and fakes no attempt, exactly like the accepted budget refusal. A replay is
  the recorded failure, never a second dispatch.
* the fallback attempt is reserved and settled at its *own* profile's price
  version, not the primary's.
* a complete fallback answer that later fails platform validation still settles
  its real usage at the fallback's price version.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from support import t041, t044, t045, t046


def configure_primary(
    stack,
    owner,
    *,
    model_id: str = "stub-model",
    fallback_ids: list[str] | None = None,
    **overrides,
) -> str:
    """Point the golden primary at a model id and a compatible fallback list."""
    t045.set_profile(
        stack.dbname,
        t041.GOLDEN_MODEL_ID,
        model_id=model_id,
        config=t045.compatible_config(fallback_ids, **overrides),
    )
    return t041.GOLDEN_MODEL_ID


def stub_usage(params: dict) -> tuple[int, int]:
    """The dev stub's documented usage derivation, computed independently.

    ``stubAgentResponse`` reports ``input_tokens = ceil(len(JCS(params))/4)`` and
    ``output_tokens = ceil(len("stub-agent:" + sha256(JCS(params))[:16])/4)``.
    """
    canonical = json.dumps(
        params, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    tokens_in = (len(canonical) + 3) // 4
    text = "stub-agent:" + hashlib.sha256(canonical).hexdigest()[:16]
    tokens_out = (len(text) + 3) // 4
    return tokens_in, tokens_out


def test_an_adapter_class_primary_fault_is_not_retried_before_the_fallback(f06_stack) -> None:
    """``adapter_unavailable`` is availability, not a retryable transient.

    The same fault already costs exactly one physical attempt on the fallback
    path and one generation on the streaming path. Before C2.01 the synchronous
    primary retried it three times because "retryable" and "may fall back" were
    decided by one predicate: the delivery mode changed the attempt count.
    """
    owner = t041.owner(f06_stack)
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id, model_id="stub-model"
    )
    configure_primary(f06_stack, owner, model_id="stub-adapter-down-primary",
                      fallback_ids=[fallback_id])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease,
                           operation_id=operation_id, params=t041.model_params())
    assert response.status == 200, response.text
    assert response.json["data"]["fallback"] == {
        "primary_profile_version_id": t041.GOLDEN_MODEL_ID,
        "profile_version_id": fallback_id,
        "reason": "adapter_unavailable",
    }, response.json

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2], attempts
    assert [row["state"] for row in attempts] == ["failed", "succeeded"], attempts
    assert attempts[0]["error_code"] == "adapter_unavailable", attempts[0]
    assert attempts[0]["settlement_state"] == "released", attempts[0]
    assert attempts[0]["charged_amount"] is None, attempts[0]
    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "succeeded", operation
    assert operation["fallback_reason"] == "adapter_unavailable", operation
    item = t041.ledger_items(owner.creator, run["id"])[0]
    assert item["attempts"] == 2, item
    assert item["fallback_reason"] == "adapter_unavailable", item

    # The streaming path already spends one physical primary generation on this
    # accepted class; the synchronous path must not spend three.
    stream_operation = t041.new_uuid()
    stream = t044.stream(
        t041.worker_client(f06_stack), lease, operation_id=stream_operation,
        logical_key="stream-1", params=t041.model_params(stub_stream_chunks=3),
    )
    assert stream.status == 200, stream.text
    frames = t044.parse_sse(stream.text)
    assert [frame["type"] for frame in frames] == [
        "error", "delta", "delta", "delta", "final",
    ], frames
    errors = t044.errors(frames)
    assert errors[0]["error"]["code"] == "adapter_unavailable", errors
    assert errors[0]["retryable"] is True, errors
    streaming_attempts = t041.attempt_rows(f06_stack.dbname, stream_operation)
    assert [row["state"] for row in streaming_attempts] == ["failed", "succeeded"], streaming_attempts
    assert streaming_attempts[0]["error_code"] == "adapter_unavailable", streaming_attempts[0]


def test_a_pre_dispatch_price_refusal_closes_the_operation_without_an_attempt(f06_stack) -> None:
    """A refused reservation must not leave a phantom ``authorized`` operation.

    The accepted budget refusal closes the operation with its typed code and
    writes no attempt. The fallback path routed a pre-dispatch price failure the
    same way; the primary path left the operation ``authorized`` forever, so a
    replay answered ``operation_in_progress`` for a call that never happened.
    """
    owner = t041.owner(f06_stack)
    config = t045.compatible_config()
    config["price"] = {
        "currency": "USD",
        "input_per_million": "not-a-decimal",
        "output_per_million": "2",
    }
    t045.set_profile(f06_stack.dbname, t041.GOLDEN_MODEL_ID, model_id="stub-model", config=config)
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    params = t041.model_params()
    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease,
                           operation_id=operation_id, params=params)
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "budget_price_invalid", response.text

    operation = t041.operation_row(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "budget_price_invalid", operation
    assert t041.attempt_count(f06_stack.dbname, operation_id) == 0
    assert t046.ledger_rows(f06_stack.dbname, run["id"]) == []

    item = t041.ledger_items(owner.creator, run["id"])[0]
    assert item["state"] == "failed", item
    assert item["error_code"] == "budget_price_invalid", item
    assert item["attempts"] == 0, item
    assert item["receipt_artifact_id"] is None, item

    # Replay is the recorded refusal: no second dispatch, no fabricated attempt.
    replay = t041.invoke(t041.worker_client(f06_stack), lease,
                         operation_id=operation_id, params=params)
    assert replay.status == 200, replay.text
    assert replay.json["state"] == "failed", replay.text
    assert replay.json["data"]["error_code"] == "budget_price_invalid", replay.text
    assert t041.attempt_count(f06_stack.dbname, operation_id) == 0
    assert t046.ledger_rows(f06_stack.dbname, run["id"]) == []


def test_the_fallback_attempt_is_priced_with_its_own_profile(f06_stack) -> None:
    """Every attempt settles at the actual candidate's price version."""
    owner = t041.owner(f06_stack)
    fallback_config = t045.compatible_config()
    fallback_config["price"] = {
        "currency": "USD",
        "input_per_million": "10",
        "output_per_million": "20",
    }
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id,
        model_id="stub-model", config=fallback_config,
    )
    configure_primary(f06_stack, owner, model_id="stub-unavailable-primary",
                      fallback_ids=[fallback_id])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    params = t041.model_params()
    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease,
                           operation_id=operation_id, params=params)
    assert response.status == 200, response.text

    primary_price = f"price-v1:{t041.GOLDEN_MODEL_ID}:in=1:out=2:USD"
    fallback_price = f"price-v1:{fallback_id}:in=10:out=20:USD"
    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2, 3, 4], attempts
    for failed in attempts[:3]:
        assert failed["price_version"] == primary_price, failed
    succeeded = attempts[3]
    assert succeeded["price_version"] == fallback_price, succeeded

    usage = response.json["data"]["usage"]
    charge = t046.expected_charge(
        usage["input_tokens"], usage["output_tokens"],
        input_per_million="10", output_per_million="20",
    )
    assert Decimal(succeeded["charged_amount"]) == charge, succeeded

    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    reserves = [row for row in rows if row["kind"] == "reserve"]
    assert len(reserves) == 4, rows
    assert [row["price_version"] for row in reserves].count(primary_price) == 3, rows
    assert [row["price_version"] for row in reserves].count(fallback_price) == 1, rows
    charged = [row for row in rows if row["kind"] == "charge"]
    assert len(charged) == 1, rows
    assert charged[0]["price_version"] == fallback_price, charged
    assert Decimal(charged[0]["amount"]) == charge, charged


def test_a_refused_fallback_answer_still_charges_its_real_usage(f06_stack) -> None:
    """A complete fallback answer that fails validation was really consumed."""
    owner = t041.owner(f06_stack)
    fallback_config = t045.compatible_config()
    fallback_config["price"] = {
        "currency": "USD",
        "input_per_million": "10",
        "output_per_million": "20",
    }
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id,
        model_id="stub-model", config=fallback_config,
    )
    configure_primary(f06_stack, owner, model_id="stub-unavailable-primary",
                      fallback_ids=[fallback_id])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    params = t041.model_params(
        response_schema={
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "banana"}},
        }
    )
    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease,
                           operation_id=operation_id, params=params)
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "provider_response_invalid", response.text

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2, 3, 4], attempts
    for failed in attempts[:3]:
        assert failed["settlement_state"] == "released", failed
    refused = attempts[3]
    assert refused["state"] == "failed", refused
    assert refused["settlement_state"] == "settled", refused

    tokens_in, tokens_out = stub_usage(params)
    charge = t046.expected_charge(
        tokens_in, tokens_out, input_per_million="10", output_per_million="20"
    )
    assert int(refused["tokens_in"]) == tokens_in, refused
    assert int(refused["tokens_out"]) == tokens_out, refused
    assert Decimal(refused["charged_amount"]) == charge, refused
    assert refused["price_version"] == f"price-v1:{fallback_id}:in=10:out=20:USD", refused

    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    assert [row["kind"] for row in rows].count("release") == 4, rows
    charged = [row for row in rows if row["kind"] == "charge"]
    assert len(charged) == 1, rows
    assert Decimal(charged[0]["amount"]) == charge, charged

    operation = t041.operation_row(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "provider_response_invalid", operation
