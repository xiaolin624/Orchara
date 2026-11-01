"""Agent loop for the P01 probe: step budget, allowlist, call accounting.

The runner is intentionally small. Its job is to produce a *structured, auditable*
result: which tools were authorized, which were actually called, how many times,
whether any call was rejected, and whether a call was replayed (a hidden retry
that the model did not declare).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import ScriptedModel
from .tools import ToolCall, ToolNotAllowed, ToolRegistry


@dataclass
class AgentResult:
    """Auditable outcome of one agent run."""

    status: str  # "completed" | "failed"
    final: str | None = None
    steps: int = 0
    tool_calls: int = 0
    allowed_tools: list[str] = field(default_factory=list)
    invoked_tools: list[str] = field(default_factory=list)
    unexpected_calls: list[dict[str, Any]] = field(default_factory=list)
    retries: dict[str, int] = field(default_factory=dict)
    structured: dict[str, Any] | None = None
    failures: list[str] = field(default_factory=list)
    model_calls: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "final": self.final,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "allowed_tools": self.allowed_tools,
            "invoked_tools": self.invoked_tools,
            "unexpected_calls": self.unexpected_calls,
            "retries": self.retries,
            "hidden_retry_detected": bool(self.retries),
            "structured": self.structured,
            "failures": self.failures,
            "model_calls": self.model_calls,
        }


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
