"""The worker-side agent loop (F06/T041).

The loop is intentionally small and dependency-free: it drives one injected model
adapter and one injected tool executor, so the real sandboxed node runtime
(T042/T043) can supply both without this module importing a vendor SDK.

Mapping rules (STATE-MACHINES §3 and the T041 brief):

* Every failure becomes a platform ``NodeOutcome`` - never a raw provider object,
  never a free-text exception message, never a hidden retry.
* Tool-call ids and arguments are re-validated here even though the server already
  did; a duplicate id, an unknown tool or bad arguments is ``failed`` with
  ``invalid_tool_call`` and is not retried.
* A tool execution that *may* have produced an external side effect but cannot be
  confirmed is ``needs_reconciliation``; an ordinary tool exception is ``failed``.
* A capability answer that requires a human approval is ``waiting``.
* Running out of turns is ``failed`` with ``max_turns_exceeded``.
* The final structured answer is validated against the declared response schema
  before it is returned.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .adapter import (
    DeclaredTool,
    Generation,
    PlatformCapabilityError,
    PlatformError,
    PlatformValidationError,
)
from .schema import validate_value

_SAFE_CODE = re.compile(r"[a-z0-9_]{1,64}\Z")


class ModelAdapter(Protocol):
    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[DeclaredTool] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> Generation:
        ...


class ToolExecutor(Protocol):
    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        ...


class ToolExecutionError(Exception):
    """A tool executor's typed failure.

    ``uncertain`` means the external effect may have happened (a write timed out
    after dispatch): the only honest outcome is ``needs_reconciliation``.
    """

    def __init__(self, code: str, *, uncertain: bool = False, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.uncertain = uncertain


@dataclass(frozen=True)
class ExecutedCall:
    id: str
    name: str


@dataclass(frozen=True)
class NodeOutcome:
    state: str
    output: dict[str, Any] | None = None
    error_code: str | None = None
    detail: str | None = None
    turns: int = 0
    tool_calls: tuple[ExecutedCall, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "output": self.output,
            "error_code": self.error_code,
            "detail": self.detail,
            "turns": self.turns,
            "tool_calls": [{"id": call.id, "name": call.name} for call in self.tool_calls],
        }


def _safe_code(code: str | None, fallback: str = "capability_failed") -> str:
    value = (code or "").strip().lower()
    return value if _SAFE_CODE.match(value) else fallback


def run_agent(
    *,
    model: ModelAdapter,
    tools: list[DeclaredTool],
    executor: ToolExecutor,
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any] | None = None,
    max_turns: int = 4,
) -> NodeOutcome:
    """Run the model/tool loop and return one platform outcome."""
    if not isinstance(max_turns, int) or max_turns < 1 or max_turns > 32:
        return NodeOutcome(state="failed", error_code="validation_failed",
                           detail="max_turns must be between 1 and 32")
    declared = {tool.name: tool for tool in tools}
    if len(declared) != len(tools):
        return NodeOutcome(state="failed", error_code="validation_failed",
                           detail="declared tool names must be unique")
    conversation = [dict(message) for message in messages]
    executed: list[ExecutedCall] = []

    for turn in range(1, max_turns + 1):
        try:
            generation = model.generate(
                messages=conversation,
                tools=tools,
                response_schema=response_schema,
            )
        except PlatformValidationError as exc:
            return NodeOutcome(
                state="failed", error_code=_safe_code(exc.code, "invalid_tool_call"),
                detail="the model answer failed platform validation", turns=turn,
                tool_calls=tuple(executed),
            )
        except PlatformCapabilityError as exc:
            if exc.code == "awaiting_approval":
                return NodeOutcome(
                    state="waiting", error_code="awaiting_approval",
                    detail="the capability requires an approval", turns=turn,
                    tool_calls=tuple(executed),
                )
            return NodeOutcome(
                state="failed", error_code=_safe_code(exc.code), turns=turn,
                detail="the capability call failed", tool_calls=tuple(executed),
            )

        if not generation.tool_calls:
            return _finish(generation, response_schema, turn, executed)

        # Re-check the server-validated projection: a duplicate id or an unknown
        # tool is a protocol defect, not a reason to retry.
        seen: set[str] = set()
        for call in generation.tool_calls:
            if not call.id or call.id in seen:
                return NodeOutcome(
                    state="failed", error_code="invalid_tool_call",
                    detail="a tool-call id is empty or duplicated", turns=turn,
                    tool_calls=tuple(executed),
                )
            seen.add(call.id)
            if call.name not in declared:
                return NodeOutcome(
                    state="failed", error_code="invalid_tool_call",
                    detail="a tool call names an undeclared tool", turns=turn,
                    tool_calls=tuple(executed),
                )
            problems = validate_value(declared[call.name].input_schema, call.arguments)
            if problems:
                return NodeOutcome(
                    state="failed", error_code="invalid_tool_call",
                    detail="a tool call argument set does not satisfy its schema", turns=turn,
                    tool_calls=tuple(executed),
                )

        tool_messages: list[dict[str, Any]] = []
        for call in generation.tool_calls:
            try:
                result = executor.execute(call.name, call.arguments)
            except ToolExecutionError as exc:
                state = "needs_reconciliation" if exc.uncertain else "failed"
                return NodeOutcome(
                    state=state, error_code=_safe_code(exc.code, "tool_execution_failed"),
                    detail="the tool execution did not complete", turns=turn,
                    tool_calls=tuple(executed),
                )
            except Exception:  # noqa: BLE001 - never leak a vendor exception body
                return NodeOutcome(
                    state="failed", error_code="tool_execution_failed",
                    detail="the tool execution raised", turns=turn,
                    tool_calls=tuple(executed),
                )
            executed.append(ExecutedCall(id=call.id, name=call.name))
            tool_messages.append({
                "role": "tool",
                "content": json.dumps(result, sort_keys=True, separators=(",", ":")),
            })
        conversation = conversation + [
            {"role": "assistant", "content": generation.text or ""},
            *tool_messages,
        ]

    return NodeOutcome(
        state="failed", error_code="max_turns_exceeded",
        detail="the agent did not finish within max_turns", turns=max_turns,
        tool_calls=tuple(executed),
    )


def _finish(
    generation: Generation,
    response_schema: dict[str, Any] | None,
    turn: int,
    executed: list[ExecutedCall],
) -> NodeOutcome:
    if response_schema is not None:
        if generation.structured is None:
            return NodeOutcome(
                state="failed", error_code="invalid_structured_output",
                detail="the answer has no structured object", turns=turn,
                tool_calls=tuple(executed),
            )
        problems = validate_value(response_schema, generation.structured)
        if problems:
            return NodeOutcome(
                state="failed", error_code="invalid_structured_output",
                detail=f"the structured answer fails the declared schema at {problems[0]['field']}",
                turns=turn, tool_calls=tuple(executed),
            )
        return NodeOutcome(state="succeeded", output=generation.structured, turns=turn,
                           tool_calls=tuple(executed))
    return NodeOutcome(
        state="succeeded", output={"text": generation.text}, turns=turn,
        tool_calls=tuple(executed),
    )


# A tiny re-export so callers do not need to import adapter internals for the
# common error path.
__all__ = [
    "DeclaredTool",
    "ExecutedCall",
    "ModelAdapter",
    "NodeOutcome",
    "PlatformCapabilityError",
    "PlatformError",
    "ToolExecutionError",
    "ToolExecutor",
    "run_agent",
]
