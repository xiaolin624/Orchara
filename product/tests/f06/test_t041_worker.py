"""F06/T041 worker adapter/agent tests.

These run without the HTTP stack: the transport is a fake that only counts and
records physical capability calls, while the tool executor calls a *real local
function*. The point of the fake is the property under test: exactly one physical
model call per ``generate`` and one per agent turn, with no hidden retry; every
ledger attempt is therefore a real transport invocation.

The same adapter is what T042/T043 will wire to the worker mTLS client; the
transport seam is the only difference.
"""

from __future__ import annotations

import uuid

import pytest

from platform_worker.agents import (
    DeclaredTool,
    PlatformCapabilityError,
    PlatformChatModel,
    PlatformValidationError,
    ToolExecutionError,
    run_agent,
)

RUN_ID = "11111111-1111-4111-8111-111111111111"
ATTEMPT = "22222222-2222-4222-8222-222222222222"
MODEL_ID = "33333333-3333-4333-8333-333333333333"
TOOL_ID = "44444444-4444-4444-8444-444444444444"

LOOKUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["city", "celsius"],
    "properties": {"city": {"type": "string"}, "celsius": {"type": "number"}},
}
REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["report"],
    "properties": {"report": {"type": "string"}},
}


class FakeTransport:
    """Counts physical capability calls and replays scripted answers."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def invoke(self, run_id: str, body: dict) -> dict:
        self.calls.append((run_id, body))
        assert self.responses, "the adapter made more physical calls than scripted"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def call_count(self) -> int:
        return len(self.calls)


def success(*, text: str = "", tool_calls=None, structured=None, stub: bool = True) -> dict:
    data = {
        "text": text,
        "tool_calls": list(tool_calls or []),
        "stub": stub,
        "usage": {"input_tokens": 3, "output_tokens": 5},
        "model_profile_version_id": MODEL_ID,
    }
    if structured is not None:
        data["structured"] = structured
    return {
        "operation_id": str(uuid.uuid4()),
        "state": "succeeded",
        "receipt_artifact_id": str(uuid.uuid4()),
        "approval_id": None,
        "data": data,
    }


def tool_call(call_id: str, name: str = "lookup", **arguments) -> dict:
    return {"id": call_id, "name": name, "arguments": dict(arguments)}


def model(transport: FakeTransport) -> PlatformChatModel:
    return PlatformChatModel(
        transport, run_id=RUN_ID, attempt_token=ATTEMPT, capability_version=MODEL_ID
    )


def test_generate_makes_exactly_one_physical_call_and_preserves_tool_calls() -> None:
    call = tool_call("call-1", city="Berlin", celsius=21)
    transport = FakeTransport(success(text="thinking", tool_calls=[call]))
    generation = model(transport).generate(
        messages=[{"role": "user", "content": "weather?"}],
        tools=[DeclaredTool(name="lookup", capability_version=TOOL_ID, input_schema=LOOKUP_SCHEMA)],
        response_schema=REPORT_SCHEMA,
    )
    assert transport.call_count == 1, "generate must make exactly one physical call"
    run_id, body = transport.calls[0]
    assert run_id == RUN_ID
    assert body["attempt_token"] == ATTEMPT
    assert body["capability_version"] == MODEL_ID
    assert body["capability_kind"] == "model"
    assert body["params"]["messages"] == [{"role": "user", "content": "weather?"}]
    assert body["params"]["tools"] == [
        {"name": "lookup", "capability_version": TOOL_ID}
    ]
    assert body["params"]["response_schema"] == REPORT_SCHEMA
    assert body["params"]["task"] == "agent"
    uuid.UUID(body["operation_id"])
    assert generation.tool_calls[0].id == "call-1"
    assert generation.tool_calls[0].arguments == {"city": "Berlin", "celsius": 21}
    assert generation.stub is True
    assert generation.usage == {"input_tokens": 3, "output_tokens": 5}


def test_generate_uses_a_distinct_operation_id_per_call() -> None:
    transport = FakeTransport(success(), success())
    chat = model(transport)
    chat.generate(messages=[{"role": "user", "content": "one"}])
    chat.generate(messages=[{"role": "user", "content": "two"}])
    first, second = (body["operation_id"] for _, body in transport.calls)
    assert first != second
    assert transport.call_count == 2


def test_platform_error_is_not_retried() -> None:
    transport = FakeTransport(PlatformCapabilityError("provider_transient", status=503))
    with pytest.raises(PlatformCapabilityError) as caught:
        model(transport).generate(messages=[{"role": "user", "content": "x"}])
    assert caught.value.code == "provider_transient"
    assert transport.call_count == 1, "the adapter must not retry a platform error"


def test_duplicate_ids_unknown_tool_and_bad_arguments_are_refused_without_retry() -> None:
    tools = [DeclaredTool(name="lookup", capability_version=TOOL_ID, input_schema=LOOKUP_SCHEMA)]
    responses = {
        "duplicate ids": success(tool_calls=[
            tool_call("same", city="Berlin", celsius=21),
            tool_call("same", city="Paris", celsius=18),
        ]),
        "unknown tool": success(tool_calls=[tool_call("call-1", "delete", city="Berlin")]),
        "bad arguments": success(tool_calls=[tool_call("call-1", city=7, celsius=21)]),
        "missing argument": success(tool_calls=[tool_call("call-1", city="Berlin")]),
    }
    for name, response in responses.items():
        transport = FakeTransport(response)
        with pytest.raises(PlatformValidationError):
            model(transport).generate(
                messages=[{"role": "user", "content": "x"}], tools=tools
            )
        assert transport.call_count == 1, f"{name} was retried"


def test_agent_executes_a_real_local_tool_and_counts_physical_calls() -> None:
    def celsius_to_fahrenheit(city: str, celsius: float) -> dict:
        return {"city": city, "fahrenheit": round(celsius * 9 / 5 + 32, 2)}

    recorded: list[tuple[str, dict]] = []

    class LocalExecutor:
        def execute(self, name: str, arguments: dict) -> dict:
            recorded.append((name, arguments))
            return celsius_to_fahrenheit(**arguments)

    transport = FakeTransport(
        success(tool_calls=[tool_call("call-1", city="Berlin", celsius=21)]),
        success(structured={"report": "Berlin is 69.8F"}, text="done"),
    )
    outcome = run_agent(
        model=model(transport),
        tools=[DeclaredTool(name="lookup", capability_version=TOOL_ID, input_schema=LOOKUP_SCHEMA)],
        executor=LocalExecutor(),
        messages=[{"role": "user", "content": "weather in Berlin?"}],
        response_schema=REPORT_SCHEMA,
        max_turns=3,
    )
    assert outcome.state == "succeeded", outcome
    assert outcome.output == {"report": "Berlin is 69.8F"}
    assert outcome.turns == 2
    assert recorded == [("lookup", {"city": "Berlin", "celsius": 21})]
    assert [(call.id, call.name) for call in outcome.tool_calls] == [("call-1", "lookup")]
    # Two turns -> two real tool results -> exactly two physical model calls.
    assert transport.call_count == 2


def test_agent_stops_at_max_turns() -> None:
    def executor(name: str, arguments: dict) -> dict:
        return {"city": arguments["city"]}

    class Executor:
        def execute(self, name: str, arguments: dict) -> dict:
            return executor(name, arguments)

    transport = FakeTransport(
        success(tool_calls=[tool_call("call-1", city="Berlin", celsius=21)]),
        success(tool_calls=[tool_call("call-2", city="Paris", celsius=18)]),
    )
    outcome = run_agent(
        model=model(transport),
        tools=[DeclaredTool(name="lookup", capability_version=TOOL_ID, input_schema=LOOKUP_SCHEMA)],
        executor=Executor(),
        messages=[{"role": "user", "content": "loop"}],
        max_turns=2,
    )
    assert outcome.state == "failed"
    assert outcome.error_code == "max_turns_exceeded"
    assert transport.call_count == 2


def test_structured_schema_error_is_not_retried() -> None:
    class UnusedExecutor:
        def execute(self, name: str, arguments: dict) -> dict:  # pragma: no cover
            raise AssertionError("no tool should run")

    transport = FakeTransport(success(structured={"report": 5}, text="bad"))
    outcome = run_agent(
        model=model(transport),
        tools=[],
        executor=UnusedExecutor(),
        messages=[{"role": "user", "content": "x"}],
        response_schema=REPORT_SCHEMA,
    )
    assert outcome.state == "failed"
    assert outcome.error_code == "invalid_structured_output"
    assert transport.call_count == 1


def test_uncertain_tool_execution_maps_to_needs_reconciliation() -> None:
    class UncertainExecutor:
        def execute(self, name: str, arguments: dict) -> dict:
            raise ToolExecutionError("tool_timeout", uncertain=True)

    transport = FakeTransport(
        success(tool_calls=[tool_call("call-1", city="Berlin", celsius=21)])
    )
    outcome = run_agent(
        model=model(transport),
        tools=[DeclaredTool(name="lookup", capability_version=TOOL_ID, input_schema=LOOKUP_SCHEMA)],
        executor=UncertainExecutor(),
        messages=[{"role": "user", "content": "x"}],
    )
    assert outcome.state == "needs_reconciliation"
    assert outcome.error_code == "tool_timeout"
    assert transport.call_count == 1


def test_awaiting_approval_maps_to_waiting() -> None:
    transport = FakeTransport(PlatformCapabilityError("awaiting_approval"))
    outcome = run_agent(
        model=model(transport),
        tools=[],
        executor=object(),  # never used
        messages=[{"role": "user", "content": "x"}],
    )
    assert outcome.state == "waiting"
    assert transport.call_count == 1


def test_adapter_rejects_a_transport_that_is_not_configured() -> None:
    transport = FakeTransport(PlatformCapabilityError("provider_not_configured", status=503))
    with pytest.raises(PlatformCapabilityError) as caught:
        model(transport).generate(messages=[{"role": "user", "content": "x"}])
    assert caught.value.code == "provider_not_configured"
    assert caught.value.status == 503
    assert transport.call_count == 1
