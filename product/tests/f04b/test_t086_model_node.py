"""F04B/T086 acceptance: the single-generation model node on the real stack.

Every case goes through the product's real path: a real draft saved through the
public API (compiled by the Go workflow compiler into the frozen execution
manifest), the real debug submission, the compiled ``cmd/supervisor exec --once``
claiming the attempt, the real T041 capability proxy over worker mTLS with the
live attempt token, the real PostgreSQL operation/ledger rows, the real sealed
capability params/receipt artifacts, the non-contract node-results read and the
owner-only SSE projection.

The model provider is the documented deterministic dev stub (``ORCHARA_PROFILE=
dev-stub``): every receipt is labelled ``stub: true`` and the output is derived
from the sealed params. There is no vendor credential and no real model call; that
boundary is recorded in the report. WSL2/runsc only supplies the deployment identity
(a model-only workflow starts no sandbox); if it is unavailable the module skips.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from support import execution_support as execution
from support import run_observations as observations
from support import t025, t027, t033, t042, t046, t086
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

DIGEST_TEXT = re.compile(r"^stub-agent:[0-9a-f]{16}$")
DIGEST_HEX = re.compile(r"^[0-9a-f]{16}$")

NODE_STATES_SUCCEEDED = ["pending", "running", "succeeded"]


@pytest.fixture(scope="module")
def sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def assert_succeeded(report: dict) -> None:
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report.get("error_code", "") == "", report
    assert report["run_state"] == "succeeded", report
    # A model-only workflow never starts a sandbox unit.
    assert report.get("sandbox") in (None, {}), report.get("sandbox")


def assert_refused(report: dict, code: str) -> None:
    """A deterministic refusal is a committed failed run with the node's code.

    The graph path certifies the attempt with ``sandbox_started=false`` and
    submits a failed proposal; the supervisor prints a failed operator report and
    exits non-zero (the attempt itself completed, nothing crashed).
    """
    assert report["status"] == "failed", report
    assert report["outcome"] == "refused", report
    assert report["error_code"] == code, report


def analyse_states(dbname: str, run_id: str) -> dict[str, list[str]]:
    return observations.node_states(dbname, run_id)


def test_json_model_node_publishes_a_validated_object_and_a_budget_ledger(
    t033_stack, sandbox
) -> None:
    definition = t086.model_definition(t086.GOLDEN_MODEL_ID, mode="json")
    owner, application_id, run = t086.queue_model_run(
        t033_stack,
        definition,
        input_value={"task": "quarterly numbers"},
        name="t086 json model",
    )
    # The same fixture the executor runs passes publish validation.
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_succeeded(report)
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    # The node published exactly one data artifact: the validated object.
    items = observations.node_result_items(owner.creator, run["id"], expected=1)
    rows = [item for item in items if item["node_id"] == "analyse"]
    assert len(rows) == 1 and rows[0]["kind"] == "data", rows
    output = observations.node_data_document(owner.creator, items, "analyse")
    assert set(output) == {"summary"}, output
    assert isinstance(output["summary"], str) and DIGEST_HEX.match(output["summary"]), output

    # The end node sealed the same object as the run's final output.
    final = observations.final_output_document(owner.creator, t033_stack.dbname, run["id"])
    assert final == output, (final, output)

    states = analyse_states(t033_stack.dbname, run["id"])
    assert states == {
        "start": NODE_STATES_SUCCEEDED,
        "analyse": NODE_STATES_SUCCEEDED,
        "end": NODE_STATES_SUCCEEDED,
    }, states
    # The owner-only SSE replay projects the same durable node.status sequence.
    assert observations.sse_node_states(t033_stack, owner.creator, run["id"]) == states

    # One controlled generation through the capability proxy: one operation, one
    # physical attempt, a deterministic logical key and the fixed profile version.
    operations = t086.operations(t033_stack.dbname, run["id"])
    assert len(operations) == 1, operations
    operation = operations[0]
    assert operation["logical_key"] == "model:analyse:1", operation
    assert operation["capability_kind"] == "model", operation
    assert operation["capability_version"] == t086.GOLDEN_MODEL_ID, operation
    assert operation["state"] == "succeeded", operation
    assert operation["receipt_artifact_id"], operation
    attempts = t086.operation_attempts(t033_stack.dbname, run["id"])
    assert len(attempts) == 1, attempts
    assert attempts[0]["attempt_no"] == 1 and attempts[0]["state"] == "succeeded", attempts[0]

    # What left the executor: task=model, one user message with only the rendered
    # bound input, and the node's output_schema as the requested response schema.
    params = t086.capability_params_document(t033_stack, run["id"])
    assert params["task"] == "model", params
    assert params["messages"] == [
        {"role": "user", "content": "Summarize quarterly numbers"}
    ], params["messages"]
    schema = params["response_schema"]
    assert schema["type"] == "object" and "summary" in schema["properties"], schema
    # The receipt is the dev stub's own labelled projection, never vendor text.
    receipt = t086.receipt_document(t033_stack, run["id"])
    assert receipt["stub"] is True, receipt
    assert receipt["model_profile_version_id"] == t086.GOLDEN_MODEL_ID, receipt
    assert receipt["text"] == "stub-agent:" + output["summary"], receipt
    assert receipt["structured"] == output, receipt

    # Budget/usage are the capability proxy's durable ledger: a zero-price dev
    # stub still reserves, releases and settles the exact amount (0).
    ledger = t046.ledger_rows(t033_stack.dbname, run["id"])
    assert sorted(row["kind"] for row in ledger) == ["charge", "release", "reserve"], ledger
    assert all(Decimal(row["amount"]) == Decimal("0") for row in ledger), ledger
    assert all(row["operation_id"] == operation["id"] for row in ledger), ledger

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_text_mode_wraps_the_provider_text_and_validates_it(t033_stack, sandbox) -> None:
    definition = t086.model_definition(t086.GOLDEN_MODEL_ID, mode="text")
    owner, _application_id, run = t086.queue_model_run(
        t033_stack,
        definition,
        input_value={"task": "quarterly numbers"},
        name="t086 text model",
    )
    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_succeeded(report)

    items = observations.node_result_items(owner.creator, run["id"], expected=1)
    output = observations.node_data_document(owner.creator, items, "analyse")
    assert set(output) == {"text"}, output
    assert DIGEST_TEXT.match(output["text"]), output
    final = observations.final_output_document(owner.creator, t033_stack.dbname, run["id"])
    assert final == output, (final, output)

    # Text mode requests no structured response: the schema is not sent and the
    # platform alone validates the wrapped {text} object.
    params = t086.capability_params_document(t033_stack, run["id"])
    assert set(params) == {"task", "messages"}, params
    assert params["messages"][0]["content"] == "Summarize quarterly numbers", params

    operations = t086.operations(t033_stack.dbname, run["id"])
    assert len(operations) == 1 and operations[0]["state"] == "succeeded", operations


def test_schema_failure_is_independent_located_and_never_retried(t033_stack, sandbox) -> None:
    # The node's response schema requires a `summary` the stub never emits in text
    # mode; the application output itself stays legal, so the failure is the
    # node's own contract check rather than a run-input/end-mapping error.
    strict_node_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["text", "summary"],
        "additionalProperties": False,
    }
    definition = t086.model_definition(
        t086.GOLDEN_MODEL_ID,
        mode="text",
        node_schema=strict_node_schema,
        execution_output_schema=dict(t086.TEXT_SCHEMA),
    )
    owner, application_id, run = t086.queue_model_run(
        t033_stack,
        definition,
        input_value={"task": "quarterly numbers"},
        name="t086 schema failure",
    )
    # Publish accepts the fixture: the schema is a legal object schema, so only a
    # real execution can prove the output violates it.
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 1, (completed.stderr, report)
    assert_refused(report, "node_output_invalid")
    assert "#/summary" in report["error"], report

    # The run failed, sealed no final output and the downstream end was skipped.
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "node_output_invalid", state
    assert state["output_artifact_id"] is None, state
    states = analyse_states(t033_stack.dbname, run["id"])
    assert states["analyse"] == ["pending", "running", "failed"], states
    assert states["end"] == ["skipped"], states

    # No success output artifact was published for the node or the run.
    assert [row for row in observations.node_result_rows(t033_stack.dbname, run["id"])
            if row["node_id"] == "analyse"] == []
    assert observations.output_artifact_id(t033_stack.dbname, run["id"]) is None

    # The independent schema failure is not retried: exactly one capability
    # operation, one physical attempt, and that call itself succeeded.
    operations = t086.operations(t033_stack.dbname, run["id"])
    assert len(operations) == 1, operations
    assert operations[0]["state"] == "succeeded", operations
    attempts = t086.operation_attempts(t033_stack.dbname, run["id"])
    assert len(attempts) == 1 and attempts[0]["attempt_no"] == 1, attempts


def test_prompt_uses_only_bound_inputs_and_rejects_expression_placeholders(
    t033_stack, sandbox
) -> None:
    # 1. A bound `{{task}}` renders exactly its value; the run's other authorized
    # input (`secret`) is not in the prompt because no template slot names it.
    prompt_input_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "secret": {"type": "string"},
        },
        "required": ["task"],
        "additionalProperties": False,
    }
    definition = t086.model_definition(
        t086.GOLDEN_MODEL_ID,
        mode="json",
        input_schema=prompt_input_schema,
    )
    owner, application_id, run = t086.queue_model_run(
        t033_stack,
        definition,
        input_value={"task": "quarterly numbers", "secret": "never-include-me"},
        name="t086 prompt binding",
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )
    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_succeeded(report)
    params = t086.capability_params_document(t033_stack, run["id"])
    assert params["messages"] == [
        {"role": "user", "content": "Summarize quarterly numbers"}
    ], params["messages"]
    sealed = json.dumps(params)
    assert "never-include-me" not in sealed, sealed
    assert "secret" not in sealed, sealed

    # 2. A property chain is not part of the DSL. The publish validator only
    # matches the strict `{{name}}` pattern, so it stays silent; the executor
    # refuses the node with a located node_prompt_invalid and spends no call.
    expression_definition = t086.model_definition(
        t086.GOLDEN_MODEL_ID, mode="json", template="{{task.amount}}"
    )
    application_id = t027.application_with_definition(
        owner.creator, expression_definition, name="t086 expression prompt"
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )
    run2 = t042.submit_linear_run(
        owner, application_id, artifact_id=None, input_value={"task": "quarterly numbers"}
    )
    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 1, (completed.stderr, report)
    assert_refused(report, "node_prompt_invalid")
    assert "/nodes/1/config/prompt_template" in report["error"], report
    state = observations.wait_run_state(t033_stack.dbname, run2["id"], {"failed"})
    assert state["error_code"] == "node_prompt_invalid", state
    states = analyse_states(t033_stack.dbname, run2["id"])
    assert states["analyse"] == ["pending", "running", "failed"], states
    assert states["end"] == ["skipped"], states
    # No operation row exists: an invalid prompt never reaches a provider.
    assert t086.operations(t033_stack.dbname, run2["id"]) == []
    assert observations.node_result_rows(t033_stack.dbname, run2["id"]) == []


def test_a_revoked_profile_is_refused_before_any_call(t033_stack, sandbox) -> None:
    # 1. The run was sealed while the profile was passed; the profile is revoked
    # before the attempt runs. The executor re-proves the fixed version and fails
    # the node with the capability proxy's model_profile_unavailable code.
    definition = t086.model_definition(t086.GOLDEN_MODEL_ID, mode="json")
    owner, _application_id, run = t086.queue_model_run(
        t033_stack,
        definition,
        input_value={"task": "quarterly numbers"},
        name="t086 revoked profile",
    )
    t086.revoke_profile(t033_stack.dbname, t086.GOLDEN_MODEL_ID)

    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 1, (completed.stderr, report)
    assert_refused(report, "model_profile_unavailable")
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "model_profile_unavailable", state
    states = analyse_states(t033_stack.dbname, run["id"])
    assert states["analyse"] == ["pending", "running", "failed"], states
    assert states["end"] == ["skipped"], states
    # Unavailable profile: no operation, no provider call, no node artifact.
    assert t086.operations(t033_stack.dbname, run["id"]) == []
    assert observations.node_result_rows(t033_stack.dbname, run["id"]) == []

    # 2. A new draft that names the revoked profile is refused at publish
    # validation and at debug submission: no Run/job is created at all.
    refused_application = t027.application_with_definition(
        owner.creator,
        t086.model_definition(t086.GOLDEN_MODEL_ID, mode="json"),
        name="t086 revoked publish",
    )
    validation = t025.validate(owner.creator, refused_application)
    assert validation.status == 200, validation.text
    errors = t025.validation_errors(validation)
    assert [error for error in errors if error["code"] == "dependency_revoked"], errors
    revision = t027.draft_revision(owner.creator, refused_application)
    refused = t027.submit_debug(
        owner.creator,
        refused_application,
        draft_revision=revision,
        input_value={"task": "x"},
        artifact_ids=[],
    )
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == "validation_failed", refused.text
    assert t086.count_runs_for_application(
        t033_stack.dbname, refused_application
    ) == 0
