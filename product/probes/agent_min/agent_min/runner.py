"""Agent loop for the P01 probe: step budget, allowlist, call accounting.

The runner is intentionally small. Its job is to produce a *structured, auditable*
result: which tools were authorized, which were actually called, how many times,
whether any call was rejected, and whether a call was replayed (a hidden retry
that the model did not declare).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .model import ScriptedModel
from .tools import ToolCall, ToolNotAllowed, ToolRegistry

RunStatus = Literal["completed", "failed"]


class AgentResult(BaseModel):
    """Auditable outcome of one agent run.

    A pydantic model on purpose: the probe runs on the locked dependency set
    (see ``requirements.lock.txt``), so the result is validated rather than
    assembled by hand. ``status`` is a literal type, not a comment.
    """

    status: RunStatus = "failed"
    final: str | None = None
    steps: int = 0
    tool_calls: int = 0
    allowed_tools: list[str] = Field(default_factory=list)
    invoked_tools: list[str] = Field(default_factory=list)
    unexpected_calls: list[dict[str, Any]] = Field(default_factory=list)
    retries: dict[str, int] = Field(default_factory=dict)
    structured: dict[str, Any] | None = None
    failures: list[str] = Field(default_factory=list)
    model_calls: int = 0

    @property
    def hidden_retry_detected(self) -> bool:
        return bool(self.retries)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump()
        payload["hidden_retry_detected"] = self.hidden_retry_detected
        return payload


def run_agent(
    model: ScriptedModel,
    registry: ToolRegistry,
    *,
    max_steps: int = 8,
) -> AgentResult:
    """Execute the scripted model against the authorized tool registry."""
    result = AgentResult(status="failed", allowed_tools=registry.allowed_names)
    seen: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}

    for step in range(max_steps):
        result.steps = step + 1
        action = model.next_action(step, registry.invocations)
        if action is None:
            result.failures.append("script exhausted before a final answer")
            break

        if action.kind == "final":
            result.status = "completed"
            result.final = action.text
            break

        if action.kind != "tool":
            result.failures.append(f"unsupported action kind {action.kind!r}")
            break

        call = ToolCall(name=action.name or "", arguments=action.arguments or {}, step=step)
        result.tool_calls += 1

        fingerprint = call.fingerprint()
        seen[fingerprint] = seen.get(fingerprint, 0) + 1
        if seen[fingerprint] > 1:
            result.retries[call.name] = result.retries.get(call.name, 0) + 1

        try:
            invocation = registry.invoke(call)
        except ToolNotAllowed as exc:
            # An unauthorized attempt must fail the run, not be skipped.
            result.unexpected_calls.append(
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "step": step,
                    "error": str(exc),
                }
            )
            result.failures.append(f"unauthorized tool call rejected: {call.name}")
            result.status = "failed"
            break
        except Exception as exc:  # tool raised: recorded, run fails honestly
            result.failures.append(
                f"tool {call.name} failed: {type(exc).__name__}: {exc}"
            )
            result.status = "failed"
            break

        if call.name not in result.invoked_tools:
            result.invoked_tools.append(call.name)
        if invocation.result is not None:
            result.structured = invocation.result

    else:
        result.failures.append(f"step budget exceeded ({max_steps})")

    result.model_calls = model.calls
    return result
