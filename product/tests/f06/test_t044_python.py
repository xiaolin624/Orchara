"""F06/T044 worker-side streaming adapter tests.

The transport is a scripted in-memory fake (the worker has no vendor adapter and
must not retry on its own); the assertions are about the adapter's contract:
forward every frame, discard the old generation, and build/execute only from the
validated final frame.
"""

from __future__ import annotations

import pytest

from platform_worker.agents import (
    DeclaredTool,
    PlatformCapabilityError,
    PlatformChatModel,
    PlatformValidationError,
    stream_agent,
)

RUN_ID = "11111111-1111-4111-8111-111111111111"
ATTEMPT_TOKEN = "22222222-2222-4222-8222-222222222222"
MODEL_VERSION = "33333333-3333-4333-8333-333333333333"
TOOL_VERSION = "44444444-4444-4444-8444-444444444444"
CITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["city"],
    "properties": {"city": {"type": "string"}},
}


def frame(kind: str, generation: str, **fields):
    payload = {
        "type": kind,
        "operation_id": "55555555-5555-4555-8555-555555555555",
        "generation_id": generation,
        **fields,
    }
    return payload


def final_frame(generation: str, *, text: str, tool_calls=None, stub: bool = True):
    return frame(
        "final",
        generation,
        data={
            "text": text,
            "tool_calls": list(tool_calls or []),
            "stub": stub,
            "model_profile_version_id": MODEL_VERSION,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
        receipt_artifact_id="66666666-6666-4666-8666-666666666666",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


class FakeStreamingTransport:
    def __init__(self, calls: list[list[dict]]) -> None:
        self.calls = calls
        self.sent: list[dict] = []

    def invoke(self, run_id, body):  # pragma: no cover - must never be called
        raise AssertionError("the streaming adapter must use stream(), not invoke()")

    def stream(self, run_id, body, on_frame):
        self.sent.append(body)
        script = self.calls[len(self.sent) - 1]
        for item in script:
            on_frame(item)


def model_for(transport) -> PlatformChatModel:
    return PlatformChatModel(
        transport,
        run_id=RUN_ID,
        attempt_token=ATTEMPT_TOKEN,
        capability_version=MODEL_VERSION,
    )


def test_old_generation_is_discarded_and_final_comes_only_from_the_current_generation() -> None:
    transport = FakeStreamingTransport([
        [
            frame("delta", "gen-old", text="old draft "),
            frame("tool_delta", "gen-old", tool_call={
                "index": 0, "id": "old-call", "name": "lookup",
                "arguments_fragment": '{"city":',
            }),
            frame("error", "gen-old", retryable=True,
                  error={"code": "provider_transient", "message": "retry"}),
            frame("delta", "gen-new", text="new "),
            frame("delta", "gen-new", text="answer"),
            final_frame("gen-new", text="new answer"),
        ],
    ])
    seen: list[dict] = []
    generation = model_for(transport).stream_generate(
        messages=[{"role": "user", "content": "hello"}],
        on_frame=seen.append,
    )
    assert generation.text == "new answer"
    assert generation.tool_calls == ()
    assert generation.stub is True
    assert [item["generation_id"] for item in seen] == [
        "gen-old", "gen-old", "gen-old", "gen-new", "gen-new", "gen-new",
    ]
    # One physical stream, no hidden retry: the server owns retries.
    assert len(transport.sent) == 1
    assert "old draft" not in generation.text


def test_stream_agent_executes_only_calls_from_the_validated_final() -> None:
    incomplete = frame("tool_delta", "gen-draft", tool_call={
        "index": 0, "id": "partial-call", "name": "lookup", "arguments_fragment": '{"city":',
    })
    complete = frame("tool_delta", "gen-final", tool_call={
        "index": 0, "id": "call-1", "name": "lookup", "arguments_fragment": '{"city":"Paris"}',
    })
    transport = FakeStreamingTransport([
        [
            frame("delta", "gen-draft", text="draft"),
            incomplete,
            frame("error", "gen-draft", retryable=True,
                  error={"code": "provider_transient", "message": "retry"}),
            frame("delta", "gen-final", text="streaming answer"),
            complete,
            final_frame("gen-final", text="streaming answer", tool_calls=[
                {"id": "call-1", "name": "lookup", "arguments": {"city": "Paris"}},
            ]),
        ],
        [
            final_frame("gen-2", text="done"),
        ],
    ])
    executed: list[tuple[str, dict]] = []

    class Executor:
        def execute(self, name, arguments):
            executed.append((name, arguments))
            return {"city": arguments["city"], "fahrenheit": 64.4}

    seen: list[dict] = []
    outcome = stream_agent(
        model=model_for(transport),
        tools=[DeclaredTool(name="lookup", capability_version=TOOL_VERSION,
                            input_schema=CITY_SCHEMA)],
        executor=Executor(),
        messages=[{"role": "user", "content": "weather?"}],
        on_frame=seen.append,
    )
    assert outcome.state == "succeeded", outcome
    assert outcome.output == {"text": "done"}
    assert executed == [("lookup", {"city": "Paris"})]
    streamed_ids = [item["tool_call"].get("id") for item in seen
                    if item["type"] == "tool_delta" and item["tool_call"].get("id")]
    # The partial fragment is forwarded for the preview but never executed.
    assert "partial-call" in streamed_ids and "call-1" in streamed_ids
    assert [call.id for call in outcome.tool_calls] == ["call-1"]
    assert len(transport.sent) == 2  # one stream per generate, no hidden retry


def test_a_frame_without_generation_identity_is_refused() -> None:
    transport = FakeStreamingTransport([
        [{"type": "delta", "operation_id": "op", "generation_id": "", "text": "x"}],
    ])
    with pytest.raises(PlatformValidationError):
        model_for(transport).stream_generate(messages=[{"role": "user", "content": "x"}])


def test_a_stream_without_a_final_is_not_success() -> None:
    transport = FakeStreamingTransport([
        [frame("delta", "gen-1", text="draft")],
    ])
    with pytest.raises(PlatformCapabilityError) as excinfo:
        model_for(transport).stream_generate(messages=[{"role": "user", "content": "x"}])
    assert excinfo.value.code == "stream_incomplete"


def test_draft_and_final_must_agree() -> None:
    transport = FakeStreamingTransport([
        [
            frame("delta", "gen-1", text="different draft"),
            final_frame("gen-1", text="final answer"),
        ],
    ])
    with pytest.raises(PlatformValidationError):
        model_for(transport).stream_generate(messages=[{"role": "user", "content": "x"}])


def test_final_tool_calls_are_validated_like_t041() -> None:
    transport = FakeStreamingTransport([
        [
            frame("delta", "gen-1", text="answer"),
            final_frame("gen-1", text="answer", tool_calls=[
                {"id": "call-1", "name": "not-declared", "arguments": {}},
            ]),
        ],
    ])
    with pytest.raises(PlatformValidationError) as excinfo:
        model_for(transport).stream_generate(
            messages=[{"role": "user", "content": "x"}],
            tools=[DeclaredTool(name="lookup", capability_version=TOOL_VERSION,
                                input_schema=CITY_SCHEMA)],
        )
    assert excinfo.value.code == "invalid_tool_call"


def test_transport_failure_is_a_single_typed_failure() -> None:
    class BrokenTransport:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, run_id, body, on_frame):
            self.calls += 1
            raise RuntimeError("connection reset")

    transport = BrokenTransport()
    with pytest.raises(PlatformCapabilityError) as excinfo:
        model_for(transport).stream_generate(messages=[{"role": "user", "content": "x"}])
    assert excinfo.value.code == "transport_error"
    assert transport.calls == 1
