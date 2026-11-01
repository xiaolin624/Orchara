"""Authorized-tool registry for the P01 minimal agent.

The probe exists to prove two things about the execution boundary:

* only tools on the explicit allowlist can run;
* every attempted call is recorded, including rejected ones, so an unexpected
  tool call cannot be silently swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

InvocationStatus = Literal["ok", "rejected", "error"]


class ToolNotAllowed(PermissionError):
    """Raised when the model asks for a tool outside the authorized set."""


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation requested by the model."""

    name: str
    arguments: dict[str, Any]
    step: int

    def fingerprint(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Stable identity used to detect replayed (retried) calls."""
        return self.name, tuple(sorted((key, repr(value)) for key, value in self.arguments.items()))


@dataclass
class ToolInvocation:
    """A recorded attempt, successful or not."""

    name: str
    arguments: dict[str, Any]
    step: int
    status: InvocationStatus
    result: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "step": self.step,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


class ToolRegistry:
    """Holds the authorized tools and the audit trail of every attempt."""

    def __init__(self, allowed: Mapping[str, Callable[[dict[str, Any]], Any]]) -> None:
        self._allowed: dict[str, Callable[[dict[str, Any]], Any]] = dict(allowed)
        self.invocations: list[ToolInvocation] = []

    @property
    def allowed_names(self) -> list[str]:
        return sorted(self._allowed)

    def invoke(self, call: ToolCall) -> ToolInvocation:
        """Run a tool call, recording the attempt before enforcing the allowlist."""
        if call.name not in self._allowed:
            invocation = ToolInvocation(
                name=call.name,
                arguments=call.arguments,
                step=call.step,
                status="rejected",
                error=f"tool {call.name!r} is not in the authorized allowlist",
            )
            self.invocations.append(invocation)
            raise ToolNotAllowed(invocation.error)

        try:
            result = self._allowed[call.name](call.arguments)
        except Exception as exc:  # recorded, then surfaced to the runner
            self.invocations.append(
                ToolInvocation(
                    name=call.name,
                    arguments=call.arguments,
                    step=call.step,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise

        invocation = ToolInvocation(
            name=call.name,
            arguments=call.arguments,
            step=call.step,
            status="ok",
            result=result,
        )
        self.invocations.append(invocation)
        return invocation


# --------------------------------------------------------------------------
# The authorized tool set (preinstalled, trusted, no network, no shell)
# --------------------------------------------------------------------------
def make_tools(dataset_dir: Path) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Build the probe's authorized tools, rooted at ``dataset_dir``."""

    def read_dataset(arguments: dict[str, Any]) -> dict[str, Any]:
        """Read a fixture dataset by NAME (never by arbitrary path)."""
        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("read_dataset requires a non-empty 'name'")
        candidate = (dataset_dir / f"{name}.json").resolve()
        if dataset_dir.resolve() not in candidate.parents:
            raise ValueError("dataset name escapes the fixture directory")
        if not candidate.exists():
            raise FileNotFoundError(f"no dataset named {name!r}")
        import json

        payload = json.loads(candidate.read_text(encoding="utf-8"))
        return {"name": name, "rows": payload["rows"]}

    def sum_amounts(arguments: dict[str, Any]) -> dict[str, Any]:
        """Sum decimal amounts exactly; floats are rejected rather than coerced."""
        rows = arguments.get("rows")
        if not isinstance(rows, list):
            raise ValueError("sum_amounts requires a list of rows")
        total = Decimal("0")
        for index, row in enumerate(rows):
            raw = row.get("amount") if isinstance(row, dict) else None
            if not isinstance(raw, str):
                raise TypeError(f"row {index} amount must be a decimal string, got {type(raw).__name__}")
            try:
                total += Decimal(raw)
            except InvalidOperation as exc:
                raise ValueError(f"row {index} amount {raw!r} is not a decimal") from exc
        return {"total": str(total), "count": len(rows)}

    return {"read_dataset": read_dataset, "sum_amounts": sum_amounts}
