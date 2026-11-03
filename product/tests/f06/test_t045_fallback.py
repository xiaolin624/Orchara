"""F06/T045 acceptance: compatible fallback after availability faults.

Every test is the real stack: PostgreSQL migrated through migration 0027, the
compiled API, the real mTLS listener with a real worker certificate, the real
sealed execution manifest of a debug run and the real filesystem object store.
The provider is the documented dev stub; a profile's registered ``model_id``
selects a deterministic fault, and the fallback is a second real profile row
frozen into the same run manifest.

The T045 gates asserted here:

* a compatible fallback answers after the primary's bounded retries, and the
  operation records profile + reason + all attempt costs (one receipt);
* the fallback is used only when kind/protocol/context/tools/schema/budget and
  manifest membership all pass;
* a permission failure is never retried and never falls back;
* a fallback that is not passed is refused at compile (dependency_not_ready);
* the fallback list belongs to the frozen manifest, not to a later DB edit.
"""

from __future__ import annotations

import pytest

from support import t025, t041, t044, t045


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


def test_compatible_fallback_after_exhausted_transient_primary(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id, model_id="stub-model"
    )
    configure_primary(f06_stack, owner, model_id="stub-unavailable-primary", fallback_ids=[fallback_id])

    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    # The fallback profile version is frozen into the run's manifest beside the
    # primary, so a later configuration edit cannot smuggle a profile in.
    manifest = t045.manifest_document(f06_stack, run["id"])
    assert set(manifest["model_profile_versions"]) == {t041.GOLDEN_MODEL_ID, fallback_id}, manifest

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease, operation_id=operation_id,
                           params=t041.model_params())
    assert response.status == 200, response.text
    assert response.json["state"] == "succeeded", response.text
    data = response.json["data"]
    assert data["fallback"] == {
        "primary_profile_version_id": t041.GOLDEN_MODEL_ID,
        "profile_version_id": fallback_id,
        "reason": "availability",
    }, data
    # The requested capability stays the primary; only the answering profile moved.
    assert data["model_profile_version_id"] == t041.GOLDEN_MODEL_ID

    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "succeeded", operation
    assert operation["capability_version"] == t041.GOLDEN_MODEL_ID
    assert operation["fallback_profile_version_id"] == fallback_id, operation
    assert operation["fallback_reason"] == "availability", operation
    assert operation["last_error_code"] is None, operation

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2, 3, 4], attempts
    assert [row["state"] for row in attempts] == ["failed", "failed", "failed", "succeeded"], attempts
    for failed in attempts[:3]:
        assert failed["error_code"] == "provider_transient", failed
        assert failed["charged_amount"] is None, failed
    succeeded = attempts[3]
    assert succeeded["tokens_in"] > 0 and succeeded["tokens_out"] > 0, succeeded
    assert succeeded["charged_amount"] is not None, succeeded
    assert succeeded["result_artifact_id"] == response.json["receipt_artifact_id"]

    # Exactly one receipt carries the switch facts; no provider payload leaks.
    receipt = t041.receipt_document(f06_stack, response.json["receipt_artifact_id"])
    assert receipt["fallback"]["profile_version_id"] == fallback_id, receipt
    assert receipt["fallback"]["reason"] == "availability", receipt

    items = t041.ledger_items(owner.creator, run["id"])
    assert len(items) == 1, items
    entry = items[0]
    assert entry["attempts"] == 4, entry
    assert entry["fallback_profile_version_id"] == fallback_id, entry
    assert entry["fallback_reason"] == "availability", entry
    assert entry["state"] == "succeeded", entry
    totals = t045.attempt_cost_totals(f06_stack.dbname, operation_id)
    assert int(totals["tokens_in"]) == entry["tokens_in"], (totals, entry)
    assert int(totals["tokens_out"]) == entry["tokens_out"], (totals, entry)
    assert t045.same_amount(totals["charged_amount"], entry["charged_amount"]), (totals, entry)
    assert t045.same_amount(
        entry["charged_amount"], str(succeeded["charged_amount"])
    ), (entry, succeeded)
    assert "params" not in entry
    assert "Summarize the sales file" not in __import__("json").dumps(entry)


def test_rate_limited_primary_records_the_rate_limit_reason(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id, model_id="stub-model"
    )
    configure_primary(f06_stack, owner, model_id="stub-rate-limited-primary",
                      fallback_ids=[fallback_id])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease, operation_id=operation_id,
                           params=t041.model_params())
    assert response.status == 200, response.text
    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["fallback_profile_version_id"] == fallback_id, operation
    assert operation["fallback_reason"] == "rate_limited", operation
    assert response.json["data"]["fallback"]["reason"] == "rate_limited"


def test_incompatible_fallback_is_never_used(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    # Same passed/unrevoked profile, wrong protocol: incompatible, not "configured".
    incompatible_id = t045.seed_profile(
        f06_stack.dbname,
        owner.creator.principal_id,
        model_id="stub-model",
        config=t045.compatible_config(protocol="embedding_compatible"),
    )
    configure_primary(f06_stack, owner, model_id="stub-unavailable-primary",
                      fallback_ids=[incompatible_id])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease, operation_id=operation_id,
                           params=t041.model_params())
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_transient", response.text

    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "failed", operation
    assert operation["fallback_profile_version_id"] is None, operation
    assert operation["fallback_reason"] is None, operation
    assert len(t041.attempt_rows(f06_stack.dbname, operation_id)) == 3
    entry = t041.ledger_items(owner.creator, run["id"])[0]
    assert "fallback_profile_version_id" not in entry, entry


def test_permission_error_is_neither_retried_nor_fallen_back(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id, model_id="stub-model"
    )
    configure_primary(f06_stack, owner, model_id="stub-permission-denied-primary",
                      fallback_ids=[fallback_id])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease, operation_id=operation_id,
                           params=t041.model_params())
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_error", response.text

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["attempt_no"] == 1 and attempts[0]["state"] == "failed", attempts
    assert attempts[0]["error_code"] == "provider_error", attempts
    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["fallback_profile_version_id"] is None, operation
    assert operation["last_error_code"] == "provider_error", operation


def test_fallback_not_in_the_frozen_manifest_is_refused(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    # The run is compiled and claimed before the primary config names a fallback.
    configure_primary(f06_stack, owner, model_id="stub-unavailable-primary")
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    late_fallback = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id, model_id="stub-model"
    )
    t045.set_profile(
        f06_stack.dbname,
        t041.GOLDEN_MODEL_ID,
        config=t045.compatible_config([late_fallback]),
    )
    manifest = t045.manifest_document(f06_stack, run["id"])
    assert set(manifest["model_profile_versions"]) == {t041.GOLDEN_MODEL_ID}, manifest

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease, operation_id=operation_id,
                           params=t041.model_params())
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_transient", response.text
    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["fallback_profile_version_id"] is None, operation
    assert len(t041.attempt_rows(f06_stack.dbname, operation_id)) == 3


def test_unpassed_fallback_is_refused_at_compile(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    pending_fallback = t045.seed_profile(
        f06_stack.dbname,
        owner.creator.principal_id,
        model_id="stub-model",
        validation_state="pending",
    )
    configure_primary(f06_stack, owner, fallback_ids=[pending_fallback])

    validation = t025.validate(owner.creator, t041.GOLDEN_APPLICATION_ID)
    errors = t025.validation_errors(validation)
    assert errors[0]["code"] == "dependency_not_ready", errors
    assert errors[0]["pointer"].endswith("/config/model_profile_version"), errors
    assert pending_fallback in errors[0]["message"], errors
    assert "fallback" in errors[0]["message"], errors


def test_budget_exhaustion_blocks_the_fallback(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id, model_id="stub-model"
    )
    configure_primary(f06_stack, owner, model_id="stub-unavailable-primary",
                      fallback_ids=[fallback_id])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    # Smaller than the fallback's conservative reservation: no fallback beyond budget.
    t045.set_run_budget(f06_stack.dbname, run["id"], "0.000001")

    operation_id = t041.new_uuid()
    response = t041.invoke(t041.worker_client(f06_stack), lease, operation_id=operation_id,
                           params=t041.model_params())
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_transient", response.text
    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "failed", operation
    assert operation["fallback_profile_version_id"] is None, operation
    assert len(t041.attempt_rows(f06_stack.dbname, operation_id)) == 3


def test_tool_call_needs_a_fallback_that_can_call_tools(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    no_tools = t045.seed_profile(
        f06_stack.dbname,
        owner.creator.principal_id,
        model_id="stub-model",
        config=t045.compatible_config(tools=False),
    )
    configure_primary(f06_stack, owner, model_id="stub-unavailable-primary",
                      fallback_ids=[no_tools])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    t041.set_tool_schema(f06_stack.dbname, t041.CITY_SCHEMA)

    operation_id = t041.new_uuid()
    response = t041.invoke(
        t041.worker_client(f06_stack), lease, operation_id=operation_id,
        params=t041.model_params(
            tools=[{"name": "lookup", "capability_version": t041.GOLDEN_TOOL_ID}]
        ),
    )
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_transient", response.text
    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["fallback_profile_version_id"] is None, operation
    assert len(t041.attempt_rows(f06_stack.dbname, operation_id)) == 3


def test_streaming_call_switches_in_a_new_generation(f06_stack) -> None:
    """The streaming path uses the same gates and never splices two answers."""
    owner = t041.owner(f06_stack)
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id, model_id="stub-model"
    )
    configure_primary(f06_stack, owner, model_id="stub-unavailable-primary",
                      fallback_ids=[fallback_id])
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    operation_id = t041.new_uuid()
    response = t044.stream(
        t041.worker_client(f06_stack), lease, operation_id=operation_id,
        params=t041.model_params(stub_stream_chunks=3),
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == [
        "error", "error", "error", "delta", "delta", "delta", "final",
    ], frames
    generations = t044.generation_ids(frames)
    assert len(generations) == 4, generations
    final = t044.finals(frames)[0]
    assert final["generation_id"] == generations[-1]
    assert final["data"]["fallback"] == {
        "primary_profile_version_id": t041.GOLDEN_MODEL_ID,
        "profile_version_id": fallback_id,
        "reason": "availability",
    }, final["data"]
    # The failed primary generations emitted no text; the fallback's text is the
    # final answer exactly (no concatenation of partial answers).
    for generation in generations[:-1]:
        assert t044.delta_text(frames, generation) == "", generation
    assert t044.delta_text(frames, generations[-1]) == final["data"]["text"]

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert [row["state"] for row in attempts] == ["failed", "failed", "failed", "succeeded"], attempts
    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["fallback_profile_version_id"] == fallback_id, operation
    assert operation["fallback_reason"] == "availability", operation
    assert t044.receipt_artifact_count(f06_stack.dbname, run["id"]) == 1
