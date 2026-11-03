"""F06/T041 end-to-end worker loop against the real capability endpoint.

This is the ticket's central claim executed end to end: the Python agent adapter
calls the real mTLS capability endpoint with a real live attempt token, the Go
server authorizes it against the run's sealed manifest, the worker executes a real
local tool function for the granted tool call, and the ledger row/attempt counts
in PostgreSQL equal the number of physical model calls the adapter made. No mock
success stands in for any of it.

Real sandboxed python/agent node execution remains T042/T043; the loop here is the
same adapter+loop those tickets will run inside the sandboxed node.
"""

from __future__ import annotations

from platform_worker.agents import (
    DeclaredTool,
    PlatformCapabilityError,
    PlatformChatModel,
    run_agent,
)

from support import f00b, t041

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


class RealCapabilityTransport:
    """The production seam implemented for the test: a real mTLS POST."""

    def __init__(self, client: f00b.InternalClient, lease: dict) -> None:
        self.client = client
        self.lease = lease
        self.calls = 0

    def invoke(self, run_id: str, body: dict) -> dict:
        self.calls += 1
        response = self.client.post(f"/internal/v1/runs/{run_id}/capabilities", body)
        if response.status != 200:
            error = response.json["error"]
            raise PlatformCapabilityError(
                error["code"], error.get("message", ""), status=response.status
            )
        return response.json


class FirstTurnToolHint:
    """Adds the documented stub tool-call hint to the first physical call only.

    The production adapter never invents a tool call; this wrapper is the test's
    scripted model script, equivalent to a vendor that chooses to call the tool on
    its first turn and answer on its second.
    """

    def __init__(self, inner: RealCapabilityTransport, hint: dict) -> None:
        self.inner = inner
        self.hint = hint

    def invoke(self, run_id: str, body: dict) -> dict:
        if self.inner.calls == 0:
            body["params"]["stub_tool_calls"] = [self.hint]
        return self.inner.invoke(run_id, body)

    @property
    def calls(self) -> int:
        return self.inner.calls


def celsius_to_fahrenheit(city: str, celsius: float) -> dict:
    return {"city": city, "fahrenheit": round(celsius * 9 / 5 + 32, 2)}


class LocalToolExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        return celsius_to_fahrenheit(**arguments)


def test_agent_loop_over_real_endpoint_matches_the_ledger(f06_stack) -> None:
    owner, run, lease = t041.claimed_run(f06_stack)
    t041.set_tool_schema(f06_stack.dbname, LOOKUP_SCHEMA)

    transport = FirstTurnToolHint(
        RealCapabilityTransport(t041.worker_client(f06_stack), lease),
        {
            "id": "call-weather-1",
            "name": "lookup",
            "arguments": {"city": "Berlin", "celsius": 21},
        },
    )
    executor = LocalToolExecutor()
    model = PlatformChatModel(
        transport,
        run_id=run["id"],
        attempt_token=lease["attempt_token"],
        capability_version=t041.GOLDEN_MODEL_ID,
    )
    outcome = run_agent(
        model=model,
        tools=[
            DeclaredTool(
                name="lookup",
                capability_version=t041.GOLDEN_TOOL_ID,
                input_schema=LOOKUP_SCHEMA,
            )
        ],
        executor=executor,
        messages=[{"role": "user", "content": "How warm is Berlin?"}],
        response_schema=REPORT_SCHEMA,
        max_turns=3,
    )

    assert outcome.state == "succeeded", outcome.as_dict()
    assert outcome.turns == 2
    assert set(outcome.output) == {"report"}
    assert executor.calls == [("lookup", {"city": "Berlin", "celsius": 21})]
    assert [(call.id, call.name) for call in outcome.tool_calls] == [
        ("call-weather-1", "lookup")
    ]

    # Two generate() calls -> two real capability operations -> two real
    # operation_attempts rows. The ledger agrees with the physical call count.
    assert transport.calls == 2
    operations = t041.operation_rows(f06_stack.dbname, run["id"])
    assert len(operations) == transport.calls, operations
    total_attempts = 0
    for operation in operations:
        assert operation["state"] == "succeeded", operation
        attempts = t041.attempt_rows(f06_stack.dbname, operation["id"])
        assert len(attempts) == 1, attempts
        assert attempts[0]["state"] == "succeeded"
        total_attempts += len(attempts)
    assert total_attempts == transport.calls
    # The first turn's params carried the declared tool; the ledger only exposes
    # a summary, never the params.
    items = t041.ledger_items(owner.creator, run["id"])
    assert len(items) == 2
    assert all(item["attempts"] == 1 and item["stub"] for item in items)
    assert any(item["tool_call_summary"] == [{"id": "call-weather-1", "name": "lookup"}]
               for item in items)
    assert all(item["structured_output_keys"] == ["report"] for item in items)


def test_agent_surfaces_a_real_endpoint_refusal_without_retrying(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    transport = RealCapabilityTransport(t041.worker_client(f06_stack), lease)
    model = PlatformChatModel(
        transport,
        run_id=run["id"],
        attempt_token=lease["attempt_token"],
        # A capability version that is not in the run's sealed manifest.
        capability_version=t041.new_uuid(),
    )
    outcome = run_agent(
        model=model,
        tools=[],
        executor=object(),
        messages=[{"role": "user", "content": "should be refused"}],
    )
    assert outcome.state == "failed"
    assert outcome.error_code == "capability_not_allowed"
    assert transport.calls == 1, "a refused call must not be retried by the worker"
    assert t041.operation_rows(f06_stack.dbname, run["id"]) == []
